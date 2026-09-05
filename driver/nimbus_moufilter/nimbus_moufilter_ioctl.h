/*
 * nimbus_moufilter_ioctl.h
 *
 * Public interface of the Nimbus Mouse Filter, shared between the kernel
 * driver and user mode (src/mouse_isolation_win.py mirrors these values).
 *
 * The driver is an upper filter on the mouse class. With no client attached it
 * passes every packet through unchanged. While a client holds the control
 * device open and has enabled isolation, packets from every physical mouse
 * are withheld from mouclass (so neither the cursor, Raw Input, nor any game
 * sees them) and are delivered to the client through ReadFile instead.
 *
 * Reads return an array of MOUSE_INPUT_DATA (ntddmou.h, 24 bytes each):
 *   USHORT UnitId; USHORT Flags; USHORT ButtonFlags; USHORT ButtonData;
 *   ULONG RawButtons; LONG LastX; LONG LastY; ULONG ExtraInformation;
 * A read completes as soon as at least one packet is available.
 *
 * Safety: isolation is cleared when the client's handle is cleaned up (crash,
 * kill, exit) and by a watchdog when no read has been pending for
 * NIMBUS_MOUFILTER_WATCHDOG_MS while isolating.
 */
#pragma once

#define NIMBUS_MOUFILTER_DEVICE_NAME      L"\\Device\\NimbusMouseFilter"
#define NIMBUS_MOUFILTER_SYMLINK_NAME     L"\\DosDevices\\NimbusMouseFilter"
#define NIMBUS_MOUFILTER_USER_PATH        L"\\\\.\\NimbusMouseFilter"

#define NIMBUS_MOUFILTER_INTERFACE_VERSION 1
#define NIMBUS_MOUFILTER_WATCHDOG_MS       2000

#ifndef CTL_CODE
#define FILE_DEVICE_UNKNOWN 0x00000022
#define METHOD_BUFFERED     0
#define FILE_ANY_ACCESS     0
#define CTL_CODE(DeviceType, Function, Method, Access) \
    (((DeviceType) << 16) | ((Access) << 14) | ((Function) << 2) | (Method))
#endif

/* Input: ULONG (0 = pass-through, nonzero = isolate). Value 0x00222000. */
#define IOCTL_NIMBUS_SET_ISOLATION  CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)

/* Output: NIMBUS_MOUFILTER_STATUS. Value 0x00222004. */
#define IOCTL_NIMBUS_GET_STATUS     CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)

typedef struct _NIMBUS_MOUFILTER_STATUS {
    ULONG Version;            /* NIMBUS_MOUFILTER_INTERFACE_VERSION */
    ULONG Isolating;          /* 1 while packets are being withheld from mouclass */
    ULONG ConnectedMice;      /* mouse devices currently filtered */
    ULONG PendingReads;       /* client reads waiting for packets */
    ULONG PacketsCaptured;    /* handed to the client since load */
    ULONG PacketsDropped;     /* lost to ring overflow while isolating */
    ULONG PacketsPassed;      /* forwarded to mouclass since load */
    ULONG WatchdogReleases;   /* times the watchdog cleared isolation */
} NIMBUS_MOUFILTER_STATUS, *PNIMBUS_MOUFILTER_STATUS;
