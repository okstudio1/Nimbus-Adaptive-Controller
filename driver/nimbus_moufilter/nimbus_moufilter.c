/*
 * nimbus_moufilter.c
 *
 * Nimbus Mouse Filter: a KMDF upper filter on the mouse class that can hand
 * the physical mouse to Nimbus Adaptive Controller.
 *
 * Derived from Microsoft's moufiltr sample (input/moufiltr in the
 * Windows-driver-samples repository, MIT). The i8042 ISR hook of the sample
 * is removed; only the MOUSE_INPUT_DATA report chain is filtered.
 *
 * Behaviour
 * ---------
 * Pass-through by default: every packet reaches mouclass unchanged.
 *
 * While a client holds \\.\NimbusMouseFilter open and has sent
 * IOCTL_NIMBUS_SET_ISOLATION(1), packets from every filtered mouse are kept
 * from mouclass and queued for the client, which reads them with ReadFile.
 * The Windows cursor, Raw Input, and every application therefore stop seeing
 * the physical mouse; the client is the only consumer.
 *
 * Release guarantees
 * ------------------
 * - The control device is exclusive: one client at a time.
 * - Handle cleanup (crash, kill, exit) restores pass-through.
 * - A watchdog restores pass-through if no read has arrived from the client
 *   for NIMBUS_MOUFILTER_WATCHDOG_MS while isolating. It runs only while
 *   isolating. A read that has been parked for NIMBUS_MOUFILTER_TICK_MS with
 *   nothing to deliver is completed empty first (a heartbeat tick), so a
 *   client that is frozen or suspended, and therefore never issues the next
 *   read, loses the mouse too (interface v3).
 * - Reads fail with STATUS_DEVICE_NOT_READY whenever isolation is off, so a
 *   client whose read comes back that way knows the mouse was given back
 *   instead of waiting forever for packets that will never be captured.
 *   Every release path clears the flag under the lock and then drains the
 *   read queue, and EvtIoRead re-checks the flag after parking a read, so a
 *   read cannot be left parked across a release.
 * - The keyboard is never touched.
 *
 * Environment: kernel mode only.
 */

#include "nimbus_moufilter.h"

#ifdef ALLOC_PRAGMA
#pragma alloc_text (INIT, DriverEntry)
#pragma alloc_text (PAGE, NimbusFilter_EvtDeviceAdd)
#pragma alloc_text (PAGE, NimbusFilter_Detach)
#pragma alloc_text (PAGE, NimbusFilter_EvtDeviceSelfManagedIoCleanup)
#pragma alloc_text (PAGE, NimbusFilter_EvtDeviceContextCleanup)
#pragma alloc_text (PAGE, NimbusFilter_EvtIoInternalDeviceControl)
#pragma alloc_text (PAGE, NimbusControl_Create)
#pragma alloc_text (PAGE, NimbusControl_Delete)
#pragma alloc_text (PAGE, NimbusControl_EvtFileCleanup)
#endif

#pragma warning(push)
#pragma warning(disable:4055) /* type cast from PVOID to PSERVICE_CALLBACK_ROUTINE */
#pragma warning(disable:4152) /* function/data pointer conversion in expression */

static NIMBUS_GLOBALS g;

/* ------------------------------------------------------------------------ */
/* Isolation state helpers                                                  */
/* ------------------------------------------------------------------------ */

static VOID NimbusFailPendingReads(VOID);

/* Caller holds g.Lock. Pass-through from here on; nothing buffered survives. */
static VOID
NimbusReleaseLocked(
    VOID
    )
{
    g.Isolating = 0;
    g.RingHead = 0;
    g.RingCount = 0;
}

/*
 * The one way isolation goes off. Clears the state under the lock, then fails
 * every read that is parked in the queue, so no read outlives a release (see
 * NimbusControl_EvtIoRead for the read that races this).
 * Callable at IRQL <= DISPATCH_LEVEL.
 */
static VOID
NimbusReleaseIsolation(
    VOID
    )
{
    WdfSpinLockAcquire(g.Lock);
    NimbusReleaseLocked();
    WdfSpinLockRelease(g.Lock);
    NimbusFailPendingReads();
}

/*
 * Turn isolation on for the current control device. Fails when the control
 * device is being torn down, so a client cannot leave the flag set with no
 * queue and no watchdog behind it. The ring is emptied so a packet that raced
 * the previous release is not replayed into this session.
 */
static BOOLEAN
NimbusStartIsolating(
    VOID
    )
{
    BOOLEAN started;

    WdfSpinLockAcquire(g.Lock);
    started = (g.ReadQueue != NULL);
    if (started) {
        g.Isolating = 1;
        g.RingHead = 0;
        g.RingCount = 0;
        g.LastReadActivity = KeQueryInterruptTime();
    }
    WdfSpinLockRelease(g.Lock);
    return started;
}

/*
 * Copy queued packets into one pending read and complete it.
 * Callable at IRQL <= DISPATCH_LEVEL.
 */
static VOID
NimbusCompleteRead(
    _In_ WDFREQUEST Request
    )
{
    NTSTATUS status;
    PMOUSE_INPUT_DATA out = NULL;
    size_t outLength = 0;
    ULONG capacity;
    ULONG n = 0;

    status = WdfRequestRetrieveOutputBuffer(Request, sizeof(MOUSE_INPUT_DATA), (PVOID *)&out, &outLength);
    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return;
    }
    capacity = (ULONG)(outLength / sizeof(MOUSE_INPUT_DATA));

    WdfSpinLockAcquire(g.Lock);
    while (n < capacity && g.RingCount > 0) {
        out[n++] = g.Ring[g.RingHead];
        g.RingHead = (g.RingHead + 1) % NIMBUS_RING_CAPACITY;
        g.RingCount--;
    }
    g.LastReadActivity = KeQueryInterruptTime();
    WdfSpinLockRelease(g.Lock);

    WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, (ULONG_PTR)n * sizeof(MOUSE_INPUT_DATA));
}

/*
 * Hand queued packets to pending reads until either runs out.
 * Callable at IRQL <= DISPATCH_LEVEL.
 *
 * The queue handle is used outside g.Lock, so it is used under QueueRundown:
 * NimbusControl_Delete clears g.ReadQueue and then waits for the rundown
 * before it deletes the queue, so a handle copied out here stays valid.
 */
static VOID
NimbusServicePendingReads(
    VOID
    )
{
    WDFQUEUE queue;
    WDFREQUEST request;
    NTSTATUS status;
    BOOLEAN haveData;

    if (!ExAcquireRundownProtection(&g.QueueRundown)) {
        return;   /* the control device is going away; Delete purges the queue */
    }
    for (;;) {
        WdfSpinLockAcquire(g.Lock);
        queue = g.ReadQueue;
        haveData = (g.RingCount > 0);
        WdfSpinLockRelease(g.Lock);

        if (queue == NULL || !haveData) {
            break;
        }
        status = WdfIoQueueRetrieveNextRequest(queue, &request);
        if (!NT_SUCCESS(status)) {
            break;   /* nobody is waiting; packets stay in the ring */
        }
        NimbusCompleteRead(request);
    }
    ExReleaseRundownProtection(&g.QueueRundown);
}

/*
 * Fail every pending read: isolation is off, so no packet will ever arrive
 * for them. Callable at IRQL <= DISPATCH_LEVEL. Same rundown rule as above.
 */
static VOID
NimbusFailPendingReads(
    VOID
    )
{
    WDFQUEUE queue;
    WDFREQUEST request;

    if (!ExAcquireRundownProtection(&g.QueueRundown)) {
        return;
    }
    WdfSpinLockAcquire(g.Lock);
    queue = g.ReadQueue;
    WdfSpinLockRelease(g.Lock);

    if (queue != NULL) {
        while (NT_SUCCESS(WdfIoQueueRetrieveNextRequest(queue, &request))) {
            WdfRequestCompleteWithInformation(request, STATUS_DEVICE_NOT_READY, 0);
        }
    }
    ExReleaseRundownProtection(&g.QueueRundown);
}

/*
 * Heartbeat: hand one parked read back empty so the client has to issue a
 * new one. LastReadActivity is deliberately left alone; the client's next
 * read refreshes it, and a client that never sends one is released by the
 * watchdog. Callable at IRQL <= DISPATCH_LEVEL. Same rundown rule as above.
 */
static VOID
NimbusTickOneRead(
    VOID
    )
{
    WDFQUEUE queue;
    WDFREQUEST request;

    if (!ExAcquireRundownProtection(&g.QueueRundown)) {
        return;
    }
    WdfSpinLockAcquire(g.Lock);
    queue = g.ReadQueue;
    WdfSpinLockRelease(g.Lock);

    if (queue != NULL && NT_SUCCESS(WdfIoQueueRetrieveNextRequest(queue, &request))) {
        WdfRequestCompleteWithInformation(request, STATUS_SUCCESS, 0);
    }
    ExReleaseRundownProtection(&g.QueueRundown);
}

/*
 * Queue packets for the client (drop oldest on overflow), then deliver.
 * Returns FALSE, having queued nothing, if isolation is off by the time the
 * lock is held: the caller then passes the packets to mouclass. Deciding under
 * the lock is what keeps a packet that races a release from being both
 * withheld from Windows and replayed into the next session.
 */
static BOOLEAN
NimbusCapturePackets(
    _In_reads_(Count) PMOUSE_INPUT_DATA Packets,
    _In_ ULONG Count
    )
{
    ULONG i;

    WdfSpinLockAcquire(g.Lock);
    if (g.Isolating == 0) {
        WdfSpinLockRelease(g.Lock);
        return FALSE;
    }
    for (i = 0; i < Count; i++) {
        if (g.RingCount == NIMBUS_RING_CAPACITY) {
            g.RingHead = (g.RingHead + 1) % NIMBUS_RING_CAPACITY;
            g.RingCount--;
            g.PacketsDropped++;
        }
        g.Ring[(g.RingHead + g.RingCount) % NIMBUS_RING_CAPACITY] = Packets[i];
        g.RingCount++;
    }
    g.PacketsCaptured += Count;
    WdfSpinLockRelease(g.Lock);

    NimbusServicePendingReads();
    return TRUE;
}

/* ------------------------------------------------------------------------ */
/* Driver entry and the per-mouse filter                                    */
/* ------------------------------------------------------------------------ */

NTSTATUS
DriverEntry(
    IN PDRIVER_OBJECT DriverObject,
    IN PUNICODE_STRING RegistryPath
    )
{
    WDF_DRIVER_CONFIG config;
    WDF_OBJECT_ATTRIBUTES attributes;
    NTSTATUS status;

    DebugPrint(("Nimbus Mouse Filter: DriverEntry (built %s %s)\n", __DATE__, __TIME__));

    RtlZeroMemory(&g, sizeof(g));
    ExInitializeRundownProtection(&g.QueueRundown);

    WDF_DRIVER_CONFIG_INIT(&config, NimbusFilter_EvtDeviceAdd);

    status = WdfDriverCreate(DriverObject, RegistryPath, WDF_NO_OBJECT_ATTRIBUTES, &config, &g.Driver);
    if (!NT_SUCCESS(status)) {
        DebugPrint(("WdfDriverCreate failed 0x%x\n", status));
        return status;
    }

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = g.Driver;
    status = WdfSpinLockCreate(&attributes, &g.Lock);
    if (!NT_SUCCESS(status)) {
        DebugPrint(("WdfSpinLockCreate failed 0x%x\n", status));
        return status;
    }

    /* A wait lock, not a fast mutex: WdfDeviceCreate needs PASSIVE_LEVEL. */
    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = g.Driver;
    status = WdfWaitLockCreate(&attributes, &g.ControlLock);
    if (!NT_SUCCESS(status)) {
        DebugPrint(("WdfWaitLockCreate failed 0x%x\n", status));
    }
    return status;
}

NTSTATUS
NimbusFilter_EvtDeviceAdd(
    IN WDFDRIVER Driver,
    IN PWDFDEVICE_INIT DeviceInit
    )
{
    WDF_OBJECT_ATTRIBUTES deviceAttributes;
    WDF_PNPPOWER_EVENT_CALLBACKS pnpCallbacks;
    WDF_IO_QUEUE_CONFIG ioQueueConfig;
    WDFDEVICE hDevice;
    PFILTER_EXTENSION ext;
    NTSTATUS status;

    PAGED_CODE();

    WdfFdoInitSetFilter(DeviceInit);
    WdfDeviceInitSetDeviceType(DeviceInit, FILE_DEVICE_MOUSE);

    /*
     * The control device is torn down from EvtDeviceSelfManagedIoCleanup,
     * which WDF calls at PASSIVE_LEVEL after this mouse's I/O has stopped.
     * The context cleanup below is only the fallback for a device that was
     * added but never started (see NimbusFilter_Detach).
     */
    WDF_PNPPOWER_EVENT_CALLBACKS_INIT(&pnpCallbacks);
    pnpCallbacks.EvtDeviceSelfManagedIoCleanup = NimbusFilter_EvtDeviceSelfManagedIoCleanup;
    WdfDeviceInitSetPnpPowerEventCallbacks(DeviceInit, &pnpCallbacks);

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&deviceAttributes, FILTER_EXTENSION);
    deviceAttributes.EvtCleanupCallback = NimbusFilter_EvtDeviceContextCleanup;

    status = WdfDeviceCreate(&DeviceInit, &deviceAttributes, &hDevice);
    if (!NT_SUCCESS(status)) {
        DebugPrint(("WdfDeviceCreate failed 0x%x\n", status));
        return status;
    }

    /*
     * Parallel, not sequential: the port driver sends requests to the top of
     * the stack while it waits on an IOCTL, and a sequential queue deadlocks.
     */
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&ioQueueConfig, WdfIoQueueDispatchParallel);
    ioQueueConfig.EvtIoInternalDeviceControl = NimbusFilter_EvtIoInternalDeviceControl;

    status = WdfIoQueueCreate(hDevice, &ioQueueConfig, WDF_NO_OBJECT_ATTRIBUTES, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) {
        DebugPrint(("WdfIoQueueCreate failed 0x%x\n", status));
        return status;
    }

    /* The control device exists while at least one mouse is filtered. */
    ext = FilterGetData(hDevice);
    WdfWaitLockAcquire(g.ControlLock, NULL);
    g.FilterInstances++;
    ext->Counted = TRUE;
    if (g.ControlDevice == NULL) {
        status = NimbusControl_Create(Driver);
        if (!NT_SUCCESS(status)) {
            /* Keep filtering (pass-through) even if user mode cannot reach us. */
            DebugPrint(("control device creation failed 0x%x; running pass-through only\n", status));
            status = STATUS_SUCCESS;
        }
    }
    WdfWaitLockRelease(g.ControlLock);

    return status;
}

/*
 * Take one mouse out of the driver-wide counts and remove the control device
 * with the last one. Idempotent, because it runs twice per device: from
 * EvtDeviceSelfManagedIoCleanup (the normal path, PASSIVE_LEVEL, I/O stopped)
 * and again from the object's context cleanup, which is the only callback a
 * device that was added but never started will see.
 */
VOID
NimbusFilter_Detach(
    _In_ WDFDEVICE Device
    )
{
    PFILTER_EXTENSION ext = FilterGetData(Device);

    PAGED_CODE();

    if (ext->Connected) {
        ext->Connected = FALSE;
        InterlockedDecrement(&g.ConnectedMice);
    }
    if (ext->Counted) {
        ext->Counted = FALSE;
        WdfWaitLockAcquire(g.ControlLock, NULL);
        g.FilterInstances--;
        if (g.FilterInstances <= 0) {
            g.FilterInstances = 0;
            NimbusControl_Delete();
        }
        WdfWaitLockRelease(g.ControlLock);
    }
}

VOID
NimbusFilter_EvtDeviceSelfManagedIoCleanup(
    IN WDFDEVICE Device
    )
{
    PAGED_CODE();
    NimbusFilter_Detach(Device);
}

VOID
NimbusFilter_EvtDeviceContextCleanup(
    IN WDFOBJECT Device
    )
{
    PAGED_CODE();
    NimbusFilter_Detach((WDFDEVICE)Device);
}

VOID
NimbusFilter_DispatchPassThrough(
    _In_ WDFREQUEST Request,
    _In_ WDFIOTARGET Target
    )
{
    WDF_REQUEST_SEND_OPTIONS options;
    NTSTATUS status;

    WDF_REQUEST_SEND_OPTIONS_INIT(&options, WDF_REQUEST_SEND_OPTION_SEND_AND_FORGET);

    if (WdfRequestSend(Request, Target, &options) == FALSE) {
        status = WdfRequestGetStatus(Request);
        DebugPrint(("WdfRequestSend failed 0x%x\n", status));
        WdfRequestComplete(Request, status);
    }
}

VOID
NimbusFilter_EvtIoInternalDeviceControl(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t OutputBufferLength,
    IN size_t InputBufferLength,
    IN ULONG IoControlCode
    )
{
    PFILTER_EXTENSION ext;
    PCONNECT_DATA connectData;
    NTSTATUS status = STATUS_SUCCESS;
    WDFDEVICE hDevice;
    size_t length;

    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    PAGED_CODE();

    hDevice = WdfIoQueueGetDevice(Queue);
    ext = FilterGetData(hDevice);

    switch (IoControlCode) {

    case IOCTL_INTERNAL_MOUSE_CONNECT:
        /* mouclass hands us its callback; we hand back ours. One connection only. */
        if (ext->UpperConnectData.ClassService != NULL) {
            status = STATUS_SHARING_VIOLATION;
            break;
        }
        status = WdfRequestRetrieveInputBuffer(Request, sizeof(CONNECT_DATA), (PVOID *)&connectData, &length);
        if (!NT_SUCCESS(status)) {
            DebugPrint(("WdfRequestRetrieveInputBuffer failed 0x%x\n", status));
            break;
        }
        ext->UpperConnectData = *connectData;
        connectData->ClassDeviceObject = WdfDeviceWdmGetDeviceObject(hDevice);
        connectData->ClassService = NimbusFilter_ServiceCallback;
        if (!ext->Connected) {
            ext->Connected = TRUE;
            InterlockedIncrement(&g.ConnectedMice);
        }
        break;

    case IOCTL_INTERNAL_MOUSE_DISCONNECT:
        /* Same as the sample: mouclass never sends this in practice. */
        status = STATUS_NOT_IMPLEMENTED;
        break;

    default:
        break;
    }

    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return;
    }

    NimbusFilter_DispatchPassThrough(Request, WdfDeviceGetIoTarget(hDevice));
}

/*
 * Called by the port driver (mouhid, i8042prt) at DISPATCH_LEVEL with the
 * packets it wants to report. This is the one place the design lives.
 */
VOID
NimbusFilter_ServiceCallback(
    IN PDEVICE_OBJECT DeviceObject,
    IN PMOUSE_INPUT_DATA InputDataStart,
    IN PMOUSE_INPUT_DATA InputDataEnd,
    IN OUT PULONG InputDataConsumed
    )
{
    PFILTER_EXTENSION ext;
    WDFDEVICE hDevice;
    ULONG count;

    hDevice = WdfWdmDeviceGetWdfDeviceHandle(DeviceObject);
    ext = FilterGetData(hDevice);
    count = (ULONG)(InputDataEnd - InputDataStart);

    /*
     * The unlocked read keeps the pass-through path lock-free; the decision
     * that counts is made under the lock inside NimbusCapturePackets.
     */
    if (g.Isolating != 0 && NimbusCapturePackets(InputDataStart, count)) {
        *InputDataConsumed = count;
        return;
    }

    InterlockedExchangeAdd((volatile LONG *)&g.PacketsPassed, (LONG)count);

    (*(PSERVICE_CALLBACK_ROUTINE)ext->UpperConnectData.ClassService)(
        ext->UpperConnectData.ClassDeviceObject,
        InputDataStart,
        InputDataEnd,
        InputDataConsumed);
}

/* ------------------------------------------------------------------------ */
/* Control device                                                           */
/* ------------------------------------------------------------------------ */

NTSTATUS
NimbusControl_Create(
    _In_ WDFDRIVER Driver
    )
{
    PWDFDEVICE_INIT deviceInit;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_FILEOBJECT_CONFIG fileConfig;
    WDF_IO_QUEUE_CONFIG queueConfig;
    WDF_TIMER_CONFIG timerConfig;
    WDFDEVICE controlDevice = NULL;
    PCONTROL_EXTENSION ctl;
    NTSTATUS status;

    DECLARE_CONST_UNICODE_STRING(deviceName, NIMBUS_MOUFILTER_DEVICE_NAME);
    DECLARE_CONST_UNICODE_STRING(symbolicLink, NIMBUS_MOUFILTER_SYMLINK_NAME);
    /* SYSTEM and Administrators: full. Interactive users: read/write (Nimbus runs as the user). */
    DECLARE_CONST_UNICODE_STRING(sddl, L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)");

    PAGED_CODE();

    deviceInit = WdfControlDeviceInitAllocate(Driver, &sddl);
    if (deviceInit == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    WdfDeviceInitSetExclusive(deviceInit, TRUE);
    WdfDeviceInitSetIoType(deviceInit, WdfDeviceIoBuffered);

    status = WdfDeviceInitAssignName(deviceInit, &deviceName);
    if (!NT_SUCCESS(status)) {
        WdfDeviceInitFree(deviceInit);
        return status;
    }

    WDF_FILEOBJECT_CONFIG_INIT(&fileConfig, WDF_NO_EVENT_CALLBACK, WDF_NO_EVENT_CALLBACK, NimbusControl_EvtFileCleanup);
    WdfDeviceInitSetFileObjectConfig(deviceInit, &fileConfig, WDF_NO_OBJECT_ATTRIBUTES);

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, CONTROL_EXTENSION);

    status = WdfDeviceCreate(&deviceInit, &attributes, &controlDevice);
    if (!NT_SUCCESS(status)) {
        WdfDeviceInitFree(deviceInit);
        return status;
    }
    ctl = ControlGetData(controlDevice);

    status = WdfDeviceCreateSymbolicLink(controlDevice, &symbolicLink);
    if (!NT_SUCCESS(status)) {
        goto fail;
    }

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&queueConfig, WdfIoQueueDispatchParallel);
    queueConfig.EvtIoDeviceControl = NimbusControl_EvtIoDeviceControl;
    queueConfig.EvtIoRead = NimbusControl_EvtIoRead;
    status = WdfIoQueueCreate(controlDevice, &queueConfig, WDF_NO_OBJECT_ATTRIBUTES, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) {
        goto fail;
    }

    WDF_IO_QUEUE_CONFIG_INIT(&queueConfig, WdfIoQueueDispatchManual);
    status = WdfIoQueueCreate(controlDevice, &queueConfig, WDF_NO_OBJECT_ATTRIBUTES, &ctl->ReadQueue);
    if (!NT_SUCCESS(status)) {
        goto fail;
    }

    WDF_TIMER_CONFIG_INIT_PERIODIC(&timerConfig, NimbusControl_EvtWatchdog, NIMBUS_WATCHDOG_PERIOD_MS);
    timerConfig.AutomaticSerialization = FALSE;
    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = controlDevice;
    status = WdfTimerCreate(&timerConfig, &attributes, &ctl->Watchdog);
    if (!NT_SUCCESS(status)) {
        goto fail;
    }

    WdfControlFinishInitializing(controlDevice);

    /* A previous control device ran the rundown down; arm it for this one. */
    ExReInitializeRundownProtection(&g.QueueRundown);
    WdfSpinLockAcquire(g.Lock);
    NimbusReleaseLocked();   /* a new control device has no client yet */
    g.ReadQueue = ctl->ReadQueue;
    WdfSpinLockRelease(g.Lock);
    g.ControlDevice = controlDevice;

    /* The watchdog runs only while isolating; IOCTL_NIMBUS_SET_ISOLATION(1) starts it. */
    DebugPrint(("Nimbus Mouse Filter: control device ready\n"));
    return STATUS_SUCCESS;

fail:
    WdfObjectDelete(controlDevice);
    return status;
}

VOID
NimbusControl_Delete(
    VOID
    )
{
    WDFDEVICE controlDevice = g.ControlDevice;
    PCONTROL_EXTENSION ctl;

    PAGED_CODE();

    if (controlDevice == NULL) {
        return;
    }
    g.ControlDevice = NULL;
    ctl = ControlGetData(controlDevice);

    /*
     * Order matters. Clear the queue pointer under the lock so no new user
     * picks it up, then wait for anyone who already copied it out (the packet
     * path runs at DISPATCH_LEVEL under QueueRundown), then stop the watchdog
     * and only then purge and delete the queue's owner.
     */
    WdfSpinLockAcquire(g.Lock);
    NimbusReleaseLocked();
    g.ReadQueue = NULL;
    WdfSpinLockRelease(g.Lock);
    ExWaitForRundownProtectionRelease(&g.QueueRundown);

    WdfTimerStop(ctl->Watchdog, TRUE);
    WdfIoQueuePurgeSynchronously(ctl->ReadQueue);   /* parked reads complete with STATUS_CANCELLED */
    WdfObjectDelete(controlDevice);
    DebugPrint(("Nimbus Mouse Filter: control device removed\n"));
}

VOID
NimbusControl_EvtIoDeviceControl(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t OutputBufferLength,
    IN size_t InputBufferLength,
    IN ULONG IoControlCode
    )
{
    PCONTROL_EXTENSION ctl = ControlGetData(WdfIoQueueGetDevice(Queue));
    NTSTATUS status;
    size_t length;
    PULONG enable;
    PNIMBUS_MOUFILTER_STATUS report;
    ULONG queued = 0;

    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    switch (IoControlCode) {

    case IOCTL_NIMBUS_SET_ISOLATION:
        status = WdfRequestRetrieveInputBuffer(Request, sizeof(ULONG), (PVOID *)&enable, &length);
        if (!NT_SUCCESS(status)) {
            break;
        }
        if (*enable != 0) {
            if (!NimbusStartIsolating()) {
                status = STATUS_DEVICE_NOT_READY;   /* control device going away */
                break;
            }
            /*
             * The watchdog stops itself once it sees isolation off, under
             * g.Lock, and NimbusStartIsolating set the flag under that same
             * lock before this call, so this start cannot be cancelled by a
             * stop that saw the previous session end.
             */
            WdfTimerStart(ctl->Watchdog, WDF_REL_TIMEOUT_IN_MS(NIMBUS_WATCHDOG_PERIOD_MS));
        } else {
            /* Also fails the client's parked read, which is how it gets its answer. */
            NimbusReleaseIsolation();
        }
        WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, 0);
        return;

    case IOCTL_NIMBUS_GET_STATUS:
        status = WdfRequestRetrieveOutputBuffer(Request, sizeof(NIMBUS_MOUFILTER_STATUS), (PVOID *)&report, &length);
        if (!NT_SUCCESS(status)) {
            break;
        }
        WdfIoQueueGetState(ctl->ReadQueue, &queued, NULL);
        WdfSpinLockAcquire(g.Lock);
        report->Version = NIMBUS_MOUFILTER_INTERFACE_VERSION;
        report->Isolating = (ULONG)g.Isolating;
        report->ConnectedMice = (ULONG)g.ConnectedMice;
        report->PendingReads = queued;
        report->PacketsCaptured = g.PacketsCaptured;
        report->PacketsDropped = g.PacketsDropped;
        report->PacketsPassed = g.PacketsPassed;
        report->WatchdogReleases = g.WatchdogReleases;
        WdfSpinLockRelease(g.Lock);
        WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, sizeof(NIMBUS_MOUFILTER_STATUS));
        return;

    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
        break;
    }

    WdfRequestComplete(Request, status);
}

VOID
NimbusControl_EvtIoRead(
    IN WDFQUEUE Queue,
    IN WDFREQUEST Request,
    IN size_t Length
    )
{
    PCONTROL_EXTENSION ctl = ControlGetData(WdfIoQueueGetDevice(Queue));
    NTSTATUS status;
    BOOLEAN isolating;
    BOOLEAN haveData;

    if (Length < sizeof(MOUSE_INPUT_DATA)) {
        WdfRequestCompleteWithInformation(Request, STATUS_BUFFER_TOO_SMALL, 0);
        return;
    }

    WdfSpinLockAcquire(g.Lock);
    isolating = (g.Isolating != 0);
    haveData = (g.RingCount > 0);
    g.LastReadActivity = KeQueryInterruptTime();
    WdfSpinLockRelease(g.Lock);

    if (!isolating) {
        /*
         * Nothing is captured while passing through, so this read would wait
         * forever. Failing it is how the client finds out that the watchdog
         * released isolation (ERROR_NOT_READY in user mode). Every release
         * empties the ring under the lock, so no packets are lost.
         */
        WdfRequestCompleteWithInformation(Request, STATUS_DEVICE_NOT_READY, 0);
        return;
    }

    if (haveData) {
        NimbusCompleteRead(Request);
        return;
    }

    status = WdfRequestForwardToIoQueue(Request, ctl->ReadQueue);
    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return;
    }

    /*
     * Isolation may have gone off, or packets may have arrived, between the
     * check above and the forward. A release drains the queue after clearing
     * the flag, so if the flag is off now this request was either drained by
     * that release or is drained here. Either way no read is left parked.
     */
    WdfSpinLockAcquire(g.Lock);
    isolating = (g.Isolating != 0);
    WdfSpinLockRelease(g.Lock);
    if (!isolating) {
        NimbusFailPendingReads();
        return;
    }
    NimbusServicePendingReads();
}

/* The client's handle is going away: the mouse must come back. */
VOID
NimbusControl_EvtFileCleanup(
    IN WDFFILEOBJECT FileObject
    )
{
    UNREFERENCED_PARAMETER(FileObject);
    PAGED_CODE();
    NimbusReleaseIsolation();
}

/*
 * Restore pass-through if the client stops reading while isolating. Runs only
 * while isolating: SET_ISOLATION(1) starts it and it stops itself, under the
 * lock, the first time it sees isolation off.
 */
VOID
NimbusControl_EvtWatchdog(
    IN WDFTIMER Timer
    )
{
    ULONGLONG now;
    ULONGLONG idle = 0;
    ULONG queued = 0;
    ULONG inDriver = 0;
    BOOLEAN isolating;
    BOOLEAN released = FALSE;
    BOOLEAN tick = FALSE;

    WdfSpinLockAcquire(g.Lock);
    if (g.ReadQueue == NULL) {
        /* NimbusControl_Delete has started; it stops this timer itself. */
        WdfSpinLockRelease(g.Lock);
        return;
    }
    /*
     * Everything is sampled under the lock so it agrees with EvtIoRead, which
     * stamps LastReadActivity under the same lock before it forwards, and with
     * NimbusServicePendingReads, whose retrieved-but-not-yet-completed read
     * shows up in inDriver. A read is therefore either visible in one of the
     * two counts or has just refreshed the timestamp.
     */
    WdfIoQueueGetState(g.ReadQueue, &queued, &inDriver);
    now = KeQueryInterruptTime();
    if (now > g.LastReadActivity) {
        idle = now - g.LastReadActivity;
    }
    isolating = (g.Isolating != 0);
    if (isolating && queued == 0 && inDriver == 0 && idle > NIMBUS_WATCHDOG_TIMEOUT) {
        NimbusReleaseLocked();
        g.WatchdogReleases++;
        released = TRUE;
        isolating = FALSE;
    } else if (isolating && queued > 0 && idle > NIMBUS_TICK_TIMEOUT) {
        /*
         * A read has sat here for a while with nothing to deliver. Complete
         * it empty (after the lock is dropped) so the client must come back
         * with a new one; a client that cannot is caught by the branch above
         * on a later period, because a parked read no longer refreshes the
         * timestamp once it has been ticked out.
         */
        tick = TRUE;
    }
    if (!isolating) {
        /*
         * Nothing left to watch. Stopping under the lock is what makes this
         * safe against a concurrent SET_ISOLATION(1): it sets the flag under
         * this lock and starts the timer afterwards, so its start always
         * lands after this stop.
         */
        WdfTimerStop(Timer, FALSE);
    }
    WdfSpinLockRelease(g.Lock);

    if (released) {
        DebugPrint(("Nimbus Mouse Filter: watchdog released isolation\n"));
        /* A read forwarded just before the release must not stay parked. */
        NimbusFailPendingReads();
    } else if (tick) {
        NimbusTickOneRead();
    }
}

#pragma warning(pop)
