#include "ZebraUsbTap.h"

#define DEVICE_NAME L"\\Device\\ZebraUsbTap"
#define DOS_NAME    L"\\DosDevices\\ZebraUsbTap"
#define CAPTURE_CHUNK_MAX (1024 * 1024)

static LIST_ENTRY gCaptureList;
static WDFSPINLOCK gCaptureLock;
static WDFWAITLOCK gDeviceLock;
static WDFDEVICE gControlDevice;
static ULONG gDeviceCount;
static LONG gQueuedBytes;
static LONG gReservedBytes;
static ULONGLONG gCaptureGeneration;
static BOOLEAN gAcceptingCapture;
static ULONGLONG gNextSequence;
static ULONGLONG gSession;
static ULONGLONG gDroppedChunks;
static ULONGLONG gDroppedBytes;
static const LONG MAX_QUEUED_BYTES = 16 * 1024 * 1024;

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


static VOID ReleaseCaptureNode(_In_ PCAPTURE_NODE Node)
{
    if (InterlockedDecrement(&Node->References) == 0) {
        ExFreePool(Node);
    }
}

static VOID RecordDrop(ULONG Length)
{
    WdfSpinLockAcquire(gCaptureLock);
    gDroppedChunks++;
    gDroppedBytes += Length;
    WdfSpinLockRelease(gCaptureLock);
}

/* Failure to capture never changes the original print IRP. */
static VOID QueueCaptureChunk(ULONG TargetId, _In_reads_bytes_(Length) const UCHAR* Buffer, ULONG Length)
{
    PCAPTURE_NODE node;
    ULONGLONG generation;
    ULONGLONG stamp;
    LARGE_INTEGER utc;
    if (!TargetId || !Buffer || !Length || Length > CAPTURE_CHUNK_MAX) {
        RecordDrop(Length);
        return;
    }
    /* Reserve before allocating or copying so saturation remains cheap and
       concurrent allocations cannot exceed the nonpaged capture budget. */
    WdfSpinLockAcquire(gCaptureLock);
    if (!gAcceptingCapture || gQueuedBytes + gReservedBytes + (LONG)Length > MAX_QUEUED_BYTES) {
        gDroppedChunks++;
        gDroppedBytes += Length;
        WdfSpinLockRelease(gCaptureLock);
        return;
    }
    generation = gCaptureGeneration;
    gReservedBytes += (LONG)Length;
    WdfSpinLockRelease(gCaptureLock);

    stamp = KeQueryInterruptTime();
    KeQuerySystemTimePrecise(&utc);
    node = (PCAPTURE_NODE)ExAllocatePool2(POOL_FLAG_NON_PAGED,
        FIELD_OFFSET(CAPTURE_NODE, Data) + Length, 'pUZT');
    if (!node) {
        WdfSpinLockAcquire(gCaptureLock);
        gReservedBytes -= (LONG)Length;
        gDroppedChunks++;
        gDroppedBytes += Length;
        WdfSpinLockRelease(gCaptureLock);
        return;
    }
    node->References = 1; /* owned by queue */
    node->TargetId = TargetId;
    node->Length = Length;
    node->Stamp = stamp;
    node->ObservedUtc = (ULONGLONG)utc.QuadPart;
    RtlCopyMemory(node->Data, Buffer, Length);
    WdfSpinLockAcquire(gCaptureLock);
    gReservedBytes -= (LONG)Length;
    /* The last device may have been removed while the copy was in flight. */
    if (!gAcceptingCapture || generation != gCaptureGeneration) {
        gDroppedChunks++;
        gDroppedBytes += Length;
        WdfSpinLockRelease(gCaptureLock);
        ReleaseCaptureNode(node);
        return;
    }
    node->Sequence = ++gNextSequence;
    node->DroppedChunks = gDroppedChunks;
    node->DroppedBytes = gDroppedBytes;
    InsertTailList(&gCaptureList, &node->Link);
    gQueuedBytes += (LONG)Length;
    WdfSpinLockRelease(gCaptureLock);
}

static VOID QueueCapture(ULONG TargetId, _In_reads_bytes_(Length) const UCHAR* Buffer, ULONG Length)
{
    ULONG offset = 0;
    while (offset < Length) {
        ULONG remaining = Length - offset;
        ULONG chunk = remaining > CAPTURE_CHUNK_MAX ? CAPTURE_CHUNK_MAX : remaining;
        QueueCaptureChunk(TargetId, Buffer + offset, chunk);
        offset += chunk;
    }
}

/* Caller holds gDeviceLock; this device is deleted when the last PnP device is removed. */
static NTSTATUS CreateControlDevice(WDFDRIVER Driver)
{
    DECLARE_CONST_UNICODE_STRING(sddl, L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
    DECLARE_CONST_UNICODE_STRING(devName, DEVICE_NAME);
    DECLARE_CONST_UNICODE_STRING(dosName, DOS_NAME);
    PWDFDEVICE_INIT init = WdfControlDeviceInitAllocate(Driver, &sddl);
    WDFDEVICE control = NULL;
    NTSTATUS status;
    WDF_IO_QUEUE_CONFIG qcfg;
    if (!init) return STATUS_INSUFFICIENT_RESOURCES;
    WdfDeviceInitSetExclusive(init, TRUE);
    status = WdfDeviceInitAssignName(init, &devName);
    if (!NT_SUCCESS(status)) goto Error;
    status = WdfDeviceCreate(&init, WDF_NO_OBJECT_ATTRIBUTES, &control);
    if (!NT_SUCCESS(status)) goto Error;
    status = WdfDeviceCreateSymbolicLink(control, &dosName);
    if (!NT_SUCCESS(status)) goto Error;
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&qcfg, WdfIoQueueDispatchSequential);
    qcfg.EvtIoDeviceControl = ZebraEvtIoDeviceControl;
    status = WdfIoQueueCreate(control, &qcfg, WDF_NO_OBJECT_ATTRIBUTES, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) goto Error;
    WdfControlFinishInitializing(control);
    gControlDevice = control;
    return STATUS_SUCCESS;
Error:
    if (init) WdfDeviceInitFree(init);
    if (control) WdfObjectDelete(control);
    return status;
}

NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    WDF_DRIVER_CONFIG config;
    WDFDRIVER driver;
    WDF_OBJECT_ATTRIBUTES attrs;
    NTSTATUS status;
    LARGE_INTEGER stamp;
    WDF_DRIVER_CONFIG_INIT(&config, ZebraEvtDeviceAdd);
    status = WdfDriverCreate(DriverObject, RegistryPath, WDF_NO_OBJECT_ATTRIBUTES, &config, &driver);
    if (!NT_SUCCESS(status)) return status;
    InitializeListHead(&gCaptureList);
    KeQuerySystemTimePrecise(&stamp);
    gSession = (ULONGLONG)stamp.QuadPart;
    WDF_OBJECT_ATTRIBUTES_INIT(&attrs);
    attrs.ParentObject = driver;
    status = WdfSpinLockCreate(&attrs, &gCaptureLock);
    if (!NT_SUCCESS(status)) return status;
    return WdfWaitLockCreate(&attrs, &gDeviceLock);
}

NTSTATUS ZebraEvtDeviceAdd(WDFDRIVER Driver, PWDFDEVICE_INIT DeviceInit)
{
    ULONG targetId = MatchTargetId(DeviceInit);
    NTSTATUS status;
    WDF_OBJECT_ATTRIBUTES attrs;
    WDFDEVICE device;
    WdfFdoInitSetFilter(DeviceInit);
    status = WdfDeviceInitAssignWdmIrpPreprocessCallback(DeviceInit,
        ZebraEvtWdmWritePreprocess, IRP_MJ_WRITE, NULL, 0);
    if (!NT_SUCCESS(status)) return status;
    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attrs, DEVICE_CONTEXT);
    attrs.EvtCleanupCallback = ZebraEvtDeviceCleanup;
    attrs.ExecutionLevel = WdfExecutionLevelPassive;
    status = WdfDeviceCreate(&DeviceInit, &attrs, &device);
    if (!NT_SUCCESS(status)) return status;
    DeviceGetContext(device)->TargetId = targetId;
    WdfWaitLockAcquire(gDeviceLock, NULL);
    DeviceGetContext(device)->Counted = TRUE;
    if (gDeviceCount == 0) {
        WdfSpinLockAcquire(gCaptureLock);
        gCaptureGeneration++;
        gAcceptingCapture = TRUE;
        WdfSpinLockRelease(gCaptureLock);
    }
    gDeviceCount++;
    if (!gControlDevice) {
        /* Sideband setup failure must not prevent normal printing. */
        (VOID)CreateControlDevice(Driver);
    }
    WdfWaitLockRelease(gDeviceLock);
    return STATUS_SUCCESS;
}

VOID ZebraEvtDeviceCleanup(WDFOBJECT Object)
{
    PDEVICE_CONTEXT context = DeviceGetContext((WDFDEVICE)Object);
    PCAPTURE_NODE node;
    WdfWaitLockAcquire(gDeviceLock, NULL);
    if (context->Counted && --gDeviceCount == 0) {
        WdfSpinLockAcquire(gCaptureLock);
        gAcceptingCapture = FALSE;
        gCaptureGeneration++;
        WdfSpinLockRelease(gCaptureLock);
        if (gControlDevice) {
            WdfObjectDelete(gControlDevice);
            gControlDevice = NULL;
        }
        /* Last printer is gone; account for and release any unacknowledged data.
           Readers hold their own references, so an in-flight peek remains safe. */
        for (;;) {
            WdfSpinLockAcquire(gCaptureLock);
            if (IsListEmpty(&gCaptureList)) {
                WdfSpinLockRelease(gCaptureLock);
                break;
            }
            node = CONTAINING_RECORD(RemoveHeadList(&gCaptureList), CAPTURE_NODE, Link);
            gQueuedBytes -= (LONG)node->Length;
            gDroppedChunks++;
            gDroppedBytes += node->Length;
            WdfSpinLockRelease(gCaptureLock);
            ReleaseCaptureNode(node);
        }
    }
    context->Counted = FALSE;
    WdfWaitLockRelease(gDeviceLock);
}

NTSTATUS ZebraEvtWdmWritePreprocess(WDFDEVICE Device, PIRP Irp)
{
    ULONG targetId = DeviceGetContext(Device)->TargetId;
    PIO_STACK_LOCATION irpSp = IoGetCurrentIrpStackLocation(Irp);
    ULONG length = irpSp ? irpSp->Parameters.Write.Length : 0;
    const UCHAR* buffer = NULL;
    PDEVICE_OBJECT wdmDevice;
    if (length) {
        wdmDevice = WdfDeviceWdmGetDeviceObject(Device);
        if ((wdmDevice->Flags & DO_BUFFERED_IO) != 0)
            buffer = (const UCHAR*)Irp->AssociatedIrp.SystemBuffer;
        else if ((wdmDevice->Flags & DO_DIRECT_IO) != 0 && Irp->MdlAddress)
            buffer = (const UCHAR*)MmGetSystemAddressForMdlSafe(Irp->MdlAddress, NormalPagePriority);
        if (buffer && targetId) QueueCapture(targetId, buffer, length);
        else RecordDrop(length);
    }
    IoSkipCurrentIrpStackLocation(Irp);
    return WdfDeviceWdmDispatchPreprocessedIrp(Device, Irp);
}

VOID ZebraEvtIoDeviceControl(WDFQUEUE Queue, WDFREQUEST Request,
    size_t OutputBufferLength, size_t InputBufferLength, ULONG IoControlCode)
{
    NTSTATUS status;
    size_t actual;
    PCAPTURE_NODE node = NULL;
    UNREFERENCED_PARAMETER(Queue);
    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    if (IoControlCode == IOCTL_ZEBRAUSBTAP_STATS) {
        PCAPTURE_STATS_V3 out;
        status = WdfRequestRetrieveOutputBuffer(Request, sizeof(*out), (PVOID*)&out, &actual);
        if (!NT_SUCCESS(status)) { WdfRequestComplete(Request, status); return; }
        WdfSpinLockAcquire(gCaptureLock);
        out->Version = ZEBRA_PROTOCOL_VERSION; out->Reserved = 0; out->Session = gSession;
        out->QueuedBytes = (ULONGLONG)(gQueuedBytes + gReservedBytes);
        out->DroppedChunks = gDroppedChunks; out->DroppedBytes = gDroppedBytes;
        WdfSpinLockRelease(gCaptureLock);
        WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, sizeof(*out));
        return;
    }
    if (IoControlCode == IOCTL_ZEBRAUSBTAP_PEEK) {
        PCAPTURE_OUTPUT_V3 out;
        size_t needed;
        WdfSpinLockAcquire(gCaptureLock);
        if (!IsListEmpty(&gCaptureList)) {
            node = CONTAINING_RECORD(gCaptureList.Flink, CAPTURE_NODE, Link);
            InterlockedIncrement(&node->References);
        }
        WdfSpinLockRelease(gCaptureLock);
        if (!node) { WdfRequestComplete(Request, STATUS_NO_MORE_ENTRIES); return; }
        needed = FIELD_OFFSET(CAPTURE_OUTPUT_V3, Data) + node->Length;
        status = WdfRequestRetrieveOutputBuffer(Request, needed, (PVOID*)&out, &actual);
        if (NT_SUCCESS(status)) {
            out->Version = ZEBRA_PROTOCOL_VERSION; out->TargetId = node->TargetId;
            out->Length = node->Length; out->Reserved = 0;
            out->Sequence = node->Sequence; out->Session = gSession; out->Stamp = node->Stamp;
            out->ObservedUtc = node->ObservedUtc;
            out->DroppedChunks = node->DroppedChunks; out->DroppedBytes = node->DroppedBytes;
            RtlCopyMemory(out->Data, node->Data, node->Length);
            WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, needed);
        } else WdfRequestComplete(Request, status);
        ReleaseCaptureNode(node);
        return;
    }
    if (IoControlCode == IOCTL_ZEBRAUSBTAP_ACK) {
        PCAPTURE_ACK_V3 ack;
        status = WdfRequestRetrieveInputBuffer(Request, sizeof(*ack), (PVOID*)&ack, &actual);
        if (!NT_SUCCESS(status)) { WdfRequestComplete(Request, status); return; }
        WdfSpinLockAcquire(gCaptureLock);
        if (!IsListEmpty(&gCaptureList)) {
            PCAPTURE_NODE head = CONTAINING_RECORD(gCaptureList.Flink, CAPTURE_NODE, Link);
            if (ack->Session == gSession && ack->Sequence == head->Sequence) {
                RemoveHeadList(&gCaptureList);
                gQueuedBytes -= (LONG)head->Length;
                node = head;
            }
        }
        WdfSpinLockRelease(gCaptureLock);
        if (!node) { WdfRequestComplete(Request, STATUS_INVALID_PARAMETER); return; }
        ReleaseCaptureNode(node);
        WdfRequestComplete(Request, STATUS_SUCCESS);
        return;
    }
    WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
}
