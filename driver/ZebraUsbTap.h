#pragma once

#include <ntddk.h>
#include <wdf.h>

#define ZEBRA_TARGET_GK420D 1
#define ZEBRA_TARGET_ZP500  2
#define ZEBRA_PROTOCOL_VERSION 3

/* V3 keeps a chunk until a single authenticated consumer acknowledges it. */
#define IOCTL_ZEBRAUSBTAP_PEEK CTL_CODE(FILE_DEVICE_UNKNOWN, 0x805, METHOD_BUFFERED, FILE_READ_DATA)
#define IOCTL_ZEBRAUSBTAP_ACK CTL_CODE(FILE_DEVICE_UNKNOWN, 0x806, METHOD_BUFFERED, FILE_READ_DATA)
#define IOCTL_ZEBRAUSBTAP_STATS CTL_CODE(FILE_DEVICE_UNKNOWN, 0x807, METHOD_BUFFERED, FILE_READ_DATA)

typedef struct _DEVICE_CONTEXT {
    ULONG TargetId;
    BOOLEAN Counted;
} DEVICE_CONTEXT, *PDEVICE_CONTEXT;
WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(DEVICE_CONTEXT, DeviceGetContext)

typedef struct _CAPTURE_NODE {
    LIST_ENTRY Link;
    volatile LONG References; /* one queue reference plus temporary readers */
    ULONG TargetId;
    ULONG Length;
    ULONGLONG Stamp;
    ULONGLONG ObservedUtc;
    ULONGLONG Sequence;
    ULONGLONG DroppedChunks;
    ULONGLONG DroppedBytes;
    UCHAR Data[1];
} CAPTURE_NODE, *PCAPTURE_NODE;

typedef struct _CAPTURE_OUTPUT_V3 {
    ULONG Version;
    ULONG TargetId;
    ULONG Length;
    ULONG Reserved;
    ULONGLONG Sequence;
    ULONGLONG Session;
    ULONGLONG Stamp;
    ULONGLONG ObservedUtc;
    ULONGLONG DroppedChunks;
    ULONGLONG DroppedBytes;
    UCHAR Data[1];
} CAPTURE_OUTPUT_V3, *PCAPTURE_OUTPUT_V3;

typedef struct _CAPTURE_ACK_V3 {
    ULONGLONG Session;
    ULONGLONG Sequence;
} CAPTURE_ACK_V3, *PCAPTURE_ACK_V3;

typedef struct _CAPTURE_STATS_V3 {
    ULONG Version;
    ULONG Reserved;
    ULONGLONG Session;
    ULONGLONG QueuedBytes;
    ULONGLONG DroppedChunks;
    ULONGLONG DroppedBytes;
} CAPTURE_STATS_V3, *PCAPTURE_STATS_V3;

/* Keep the wire layout and CTL_CODE values in sync with server/app.py. */
C_ASSERT(FIELD_OFFSET(CAPTURE_OUTPUT_V3, Data) == 64 &&
         sizeof(CAPTURE_ACK_V3) == 16 && sizeof(CAPTURE_STATS_V3) == 40 &&
         IOCTL_ZEBRAUSBTAP_PEEK == 0x226014 &&
         IOCTL_ZEBRAUSBTAP_ACK == 0x226018 &&
         IOCTL_ZEBRAUSBTAP_STATS == 0x22601C);

DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD ZebraEvtDeviceAdd;
EVT_WDFDEVICE_WDM_IRP_PREPROCESS ZebraEvtWdmWritePreprocess;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL ZebraEvtIoDeviceControl;
EVT_WDF_OBJECT_CONTEXT_CLEANUP ZebraEvtDeviceCleanup;
