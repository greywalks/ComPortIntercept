#include "ZebraUsbTap.h"

#define DEVICE_NAME L"\\Device\\ZebraUsbTap"
#define DOS_NAME    L"\\DosDevices\\ZebraUsbTap"

#define CAPTURE_CHUNK_MAX (1024 * 1024)

static LIST_ENTRY gCaptureList;
static WDFSPINLOCK gCaptureLock;
static volatile LONG gQueuedBytes = 0;
static const LONG MAX_QUEUED_BYTES = 16 * 1024 * 1024;

/* Physical USB device IDs, directly above usbprint.sys. */
static const WCHAR GK420D_HWID[] = L"USB\\VID_0A5F&PID_0080";
static const WCHAR ZP500_HWID[]  = L"USB\\VID_0A5F&PID_0062";

static ULONG MatchTargetId(_In_ PWDFDEVICE_INIT DeviceInit)
{
    WDFMEMORY memory = NULL;
    NTSTATUS status;
    PVOID buffer = NULL;
    size_t bytes = 0;
    ULONG targetId = 0;

    status = WdfFdoInitAllocAndQueryProperty(
        DeviceInit,
        DevicePropertyHardwareID,
        PagedPool,
        WDF_NO_OBJECT_ATTRIBUTES,
        &memory
    );
    if (!NT_SUCCESS(status)) {
        return 0;
    }

    buffer = WdfMemoryGetBuffer(memory, &bytes);
    if (buffer && bytes >= sizeof(WCHAR)) {
        PWSTR current = (PWSTR)buffer;
        PWSTR end = (PWSTR)((PUCHAR)buffer + bytes);
        while (current < end && *current) {
            UNICODE_STRING value;
            UNICODE_STRING gk;
            UNICODE_STRING zp;
            RtlInitUnicodeString(&value, current);
            RtlInitUnicodeString(&gk, GK420D_HWID);
            RtlInitUnicodeString(&zp, ZP500_HWID);

            if (RtlEqualUnicodeString(&value, &gk, TRUE)) {
                targetId = ZEBRA_TARGET_GK420D;
                break;
            }
            if (RtlEqualUnicodeString(&value, &zp, TRUE)) {
                targetId = ZEBRA_TARGET_ZP500;
                break;
            }

            current += (value.Length / sizeof(WCHAR)) + 1;
        }
    }

    WdfObjectDelete(memory);
    return targetId;
}

/*
 * Capture is deliberately fail-open: allocation/mapping/queue limits can make
 * us skip archive data, but must never fail or modify the actual print IRP.
 */
static VOID QueueCaptureChunk(
    ULONG TargetId,
    _In_reads_bytes_(Length) const UCHAR* Buffer,
    ULONG Length
)
{
    SIZE_T allocSize;
    PCAPTURE_NODE node;

    if (TargetId == 0 || Buffer == NULL || Length == 0 || Length > CAPTURE_CHUNK_MAX) {
        return;
    }

    allocSize = FIELD_OFFSET(CAPTURE_NODE, Data) + Length;
    node = (PCAPTURE_NODE)ExAllocatePool2(
        POOL_FLAG_NON_PAGED,
        allocSize,
        'pUZT'
    );
    if (!node) {
        return;
    }

    node->TargetId = TargetId;
    node->Length = Length;
    node->Stamp = KeQueryInterruptTime();
    RtlCopyMemory(node->Data, Buffer, Length);

    WdfSpinLockAcquire(gCaptureLock);
    if (gQueuedBytes + (LONG)Length > MAX_QUEUED_BYTES) {
        WdfSpinLockRelease(gCaptureLock);
        ExFreePool(node);
        return;
    }
    InsertTailList(&gCaptureList, &node->Link);
    gQueuedBytes += (LONG)Length;
    WdfSpinLockRelease(gCaptureLock);
}

static VOID QueueCapture(
    ULONG TargetId,
    _In_reads_bytes_(Length) const UCHAR* Buffer,
    ULONG Length
)
{
    ULONG offset = 0;

    while (offset < Length) {
        ULONG remaining = Length - offset;
        ULONG chunk = remaining > CAPTURE_CHUNK_MAX ? CAPTURE_CHUNK_MAX : remaining;
        QueueCaptureChunk(TargetId, Buffer + offset, chunk);
        offset += chunk;
    }
}

static NTSTATUS CreateControlDevice(WDFDRIVER Driver)
{
    DECLARE_CONST_UNICODE_STRING(sddl, L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
    PWDFDEVICE_INIT init = WdfControlDeviceInitAllocate(Driver, &sddl);
    WDFDEVICE control;
    NTSTATUS status;
    WDF_IO_QUEUE_CONFIG qcfg;

    if (!init) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    {
        DECLARE_CONST_UNICODE_STRING(devName, DEVICE_NAME);
        status = WdfDeviceInitAssignName(init, &devName);
    }
    if (!NT_SUCCESS(status)) {
        WdfDeviceInitFree(init);
        return status;
    }

    status = WdfDeviceCreate(&init, WDF_NO_OBJECT_ATTRIBUTES, &control);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    {
        DECLARE_CONST_UNICODE_STRING(dosName, DOS_NAME);
        status = WdfDeviceCreateSymbolicLink(control, &dosName);
    }
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&qcfg, WdfIoQueueDispatchSequential);
    qcfg.EvtIoDeviceControl = ZebraEvtIoDeviceControl;
    status = WdfIoQueueCreate(control, &qcfg, WDF_NO_OBJECT_ATTRIBUTES, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WdfControlFinishInitializing(control);
    return STATUS_SUCCESS;
}

NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    WDF_DRIVER_CONFIG config;
    WDFDRIVER driver;
    NTSTATUS status;
    WDF_OBJECT_ATTRIBUTES attrs;

    WDF_DRIVER_CONFIG_INIT(&config, ZebraEvtDeviceAdd);
    status = WdfDriverCreate(
        DriverObject,
        RegistryPath,
        WDF_NO_OBJECT_ATTRIBUTES,
        &config,
        &driver
    );
    if (!NT_SUCCESS(status)) {
        return status;
    }

    InitializeListHead(&gCaptureList);

    WDF_OBJECT_ATTRIBUTES_INIT(&attrs);
    attrs.ParentObject = driver;
    status = WdfSpinLockCreate(&attrs, &gCaptureLock);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    return CreateControlDevice(driver);
}

NTSTATUS ZebraEvtDeviceAdd(WDFDRIVER Driver, PWDFDEVICE_INIT DeviceInit)
{
    ULONG targetId;
    NTSTATUS status;
    WDF_OBJECT_ATTRIBUTES attrs;
    WDFDEVICE device;

    UNREFERENCED_PARAMETER(Driver);

    targetId = MatchTargetId(DeviceInit);

    /*
     * Mark this as a filter BEFORE WdfDeviceCreate. Requests for which we do
     * not create an I/O queue are automatically forwarded by KMDF.
     */
    WdfFdoInitSetFilter(DeviceInit);

    /*
     * Observe IRP_MJ_WRITE before KMDF dispatches it. We copy the payload and
     * then return the same IRP to KMDF with WdfDeviceWdmDispatchPreprocessedIrp.
     * There is intentionally NO EvtIoWrite queue in this version.
     */
    status = WdfDeviceInitAssignWdmIrpPreprocessCallback(
        DeviceInit,
        ZebraEvtWdmWritePreprocess,
        IRP_MJ_WRITE,
        NULL,
        0
    );
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attrs, DEVICE_CONTEXT);
    status = WdfDeviceCreate(&DeviceInit, &attrs, &device);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    DeviceGetContext(device)->TargetId = targetId;

    /*
     * Deliberately no PnP-device I/O queue. After preprocessing, KMDF's filter
     * pass-through path forwards the unchanged write to usbprint.sys.
     */
    return STATUS_SUCCESS;
}

NTSTATUS ZebraEvtWdmWritePreprocess(WDFDEVICE Device, PIRP Irp)
{
    ULONG targetId = DeviceGetContext(Device)->TargetId;
    PIO_STACK_LOCATION irpSp = IoGetCurrentIrpStackLocation(Irp);
    ULONG length = 0;
    const UCHAR* buffer = NULL;
    PDEVICE_OBJECT wdmDevice;

    if (irpSp != NULL) {
        length = irpSp->Parameters.Write.Length;
    }

    if (targetId != 0 && length != 0) {
        wdmDevice = WdfDeviceWdmGetDeviceObject(Device);

        if ((wdmDevice->Flags & DO_BUFFERED_IO) != 0) {
            buffer = (const UCHAR*)Irp->AssociatedIrp.SystemBuffer;
        }
        else if ((wdmDevice->Flags & DO_DIRECT_IO) != 0 && Irp->MdlAddress != NULL) {
            buffer = (const UCHAR*)MmGetSystemAddressForMdlSafe(
                Irp->MdlAddress,
                NormalPagePriority
            );
        }

        /*
         * Fail open. If this stack ever presents METHOD_NEITHER-style data or
         * mapping fails, do not touch it; simply let the print proceed.
         */
        if (buffer != NULL) {
            QueueCapture(targetId, buffer, length);
        }
    }

    /*
     * Return the ORIGINAL, unmodified IRP to KMDF. Because this device was
     * declared a filter and owns no write queue, KMDF forwards it to the next
     * lower driver (usbprint.sys). Do not complete or re-format the write here.
     */
    IoSkipCurrentIrpStackLocation(Irp);
    return WdfDeviceWdmDispatchPreprocessedIrp(Device, Irp);
}

VOID ZebraEvtIoDeviceControl(
    WDFQUEUE Queue,
    WDFREQUEST Request,
    size_t OutputBufferLength,
    size_t InputBufferLength,
    ULONG IoControlCode
)
{
    PCAPTURE_NODE node = NULL;
    size_t needed;
    PCAPTURE_OUTPUT out = NULL;
    size_t actual = 0;
    NTSTATUS status;

    UNREFERENCED_PARAMETER(Queue);
    UNREFERENCED_PARAMETER(InputBufferLength);

    if (IoControlCode != IOCTL_ZEBRAUSBTAP_GET_NEXT) {
        WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
        return;
    }

    WdfSpinLockAcquire(gCaptureLock);
    if (!IsListEmpty(&gCaptureList)) {
        PLIST_ENTRY entry = RemoveHeadList(&gCaptureList);
        node = CONTAINING_RECORD(entry, CAPTURE_NODE, Link);
        gQueuedBytes -= (LONG)node->Length;
    }
    WdfSpinLockRelease(gCaptureLock);

    if (!node) {
        WdfRequestCompleteWithInformation(Request, STATUS_NO_MORE_ENTRIES, 0);
        return;
    }

    needed = FIELD_OFFSET(CAPTURE_OUTPUT, Data) + node->Length;
    if (OutputBufferLength < needed) {
        ExFreePool(node);
        WdfRequestCompleteWithInformation(Request, STATUS_BUFFER_TOO_SMALL, needed);
        return;
    }

    status = WdfRequestRetrieveOutputBuffer(
        Request,
        needed,
        (PVOID*)&out,
        &actual
    );

    if (NT_SUCCESS(status)) {
        out->TargetId = node->TargetId;
        out->Length = node->Length;
        out->Stamp = node->Stamp;
        RtlCopyMemory(out->Data, node->Data, node->Length);
        WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, needed);
    }
    else {
        WdfRequestComplete(Request, status);
    }

    ExFreePool(node);
}
