/*
 * nimbus_moufilter.h
 *
 * Private declarations for the Nimbus Mouse Filter, a KMDF upper filter on the
 * mouse class derived from Microsoft's moufiltr sample. See
 * docs/vision/WINDOWS_MOUSE_FILTER_PLAN.md for the design.
 *
 * Environment: kernel mode only.
 */
#ifndef NIMBUS_MOUFILTER_H
#define NIMBUS_MOUFILTER_H

#include <ntddk.h>
#include <kbdmou.h>
#include <ntddmou.h>
#include <wdf.h>

#include "nimbus_moufilter_ioctl.h"

#if DBG
#define DebugPrint(_x_) DbgPrint _x_
#else
#define DebugPrint(_x_)
#endif

/* Packets buffered between the mouse and the client. 1024 * 24 bytes. */
#define NIMBUS_RING_CAPACITY        1024
#define NIMBUS_WATCHDOG_PERIOD_MS   250
#define NIMBUS_WATCHDOG_TIMEOUT     ((ULONGLONG)NIMBUS_MOUFILTER_WATCHDOG_MS * 10000ULL)  /* 100 ns units */

/* Per filtered mouse. */
typedef struct _FILTER_EXTENSION {
    CONNECT_DATA UpperConnectData;   /* mouclass's callback, called when passing through */
    BOOLEAN      Connected;          /* IOCTL_INTERNAL_MOUSE_CONNECT seen */
    BOOLEAN      Counted;            /* included in g.FilterInstances */
} FILTER_EXTENSION, *PFILTER_EXTENSION;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(FILTER_EXTENSION, FilterGetData)

/* The single control device user mode opens. */
typedef struct _CONTROL_EXTENSION {
    WDFQUEUE ReadQueue;   /* manual queue holding pending client reads */
    WDFTIMER Watchdog;
} CONTROL_EXTENSION, *PCONTROL_EXTENSION;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(CONTROL_EXTENSION, ControlGetData)

/* Driver-wide state. Everything below Lock is guarded by it. */
typedef struct _NIMBUS_GLOBALS {
    WDFDRIVER   Driver;
    WDFWAITLOCK ControlLock;       /* PASSIVE-level lock: keeps IRQL at PASSIVE while creating devices */
    WDFDEVICE   ControlDevice;     /* guarded by ControlLock */
    LONG        FilterInstances;   /* guarded by ControlLock */

    WDFSPINLOCK Lock;
    WDFQUEUE    ReadQueue;         /* copy of the control device's queue, NULL when absent */
    LONG        Isolating;
    LONG        ConnectedMice;
    ULONGLONG   LastReadActivity;  /* KeQueryInterruptTime() of the last read arrival or completion */
    ULONG       PacketsCaptured;
    ULONG       PacketsDropped;
    ULONG       PacketsPassed;
    ULONG       WatchdogReleases;
    ULONG       RingHead;
    ULONG       RingCount;
    MOUSE_INPUT_DATA Ring[NIMBUS_RING_CAPACITY];
} NIMBUS_GLOBALS, *PNIMBUS_GLOBALS;

DRIVER_INITIALIZE DriverEntry;

EVT_WDF_DRIVER_DEVICE_ADD                    NimbusFilter_EvtDeviceAdd;
EVT_WDF_OBJECT_CONTEXT_CLEANUP               NimbusFilter_EvtDeviceContextCleanup;
EVT_WDF_IO_QUEUE_IO_INTERNAL_DEVICE_CONTROL  NimbusFilter_EvtIoInternalDeviceControl;

EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL           NimbusControl_EvtIoDeviceControl;
EVT_WDF_IO_QUEUE_IO_READ                     NimbusControl_EvtIoRead;
EVT_WDF_FILE_CLEANUP                         NimbusControl_EvtFileCleanup;
EVT_WDF_TIMER                                NimbusControl_EvtWatchdog;

VOID
NimbusFilter_DispatchPassThrough(
    _In_ WDFREQUEST Request,
    _In_ WDFIOTARGET Target
    );

VOID
NimbusFilter_ServiceCallback(
    IN PDEVICE_OBJECT DeviceObject,
    IN PMOUSE_INPUT_DATA InputDataStart,
    IN PMOUSE_INPUT_DATA InputDataEnd,
    IN OUT PULONG InputDataConsumed
    );

NTSTATUS
NimbusControl_Create(
    _In_ WDFDRIVER Driver
    );

VOID
NimbusControl_Delete(
    VOID
    );

#endif /* NIMBUS_MOUFILTER_H */
