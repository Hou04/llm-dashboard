/**
 * Icon Library — Minimal monoline SVG icons.
 * 
 * Inspired by Lucide / Heroicons.  Renders as inline SVGs — crisp at any size,
 * inherits the parent's `color` via `currentColor`.
 */

import React from 'react';

interface IconProps {
  size?: number;
  className?: string;
  style?: React.CSSProperties;
}

const I = ({ d, size = 18, className, style }: IconProps & { d: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} style={style}>
    <path d={d} />
  </svg>
);

// Multi-path variant
const Im = ({ paths, size = 18, className, style }: IconProps & { paths: string[] }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} style={style}>
    {paths.map((d, i) => <path key={i} d={d} />)}
  </svg>
);

// ── Navigation / Sidebar Icons ──

export const IconDashboard = (p: IconProps) => <Im {...p} paths={[
  "M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z",
  "M9 22V12h6v10"
]} />;

export const IconHome = (p: IconProps) => <Im {...p} paths={[
  "M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z",
  "M9 22V12h6v10"
]} />;

export const IconGateway = (p: IconProps) => <Im {...p} paths={[
  "M13 2L3 14h9l-1 8 10-12h-9l1-8z"
]} />;

export const IconCost = (p: IconProps) => <Im {...p} paths={[
  "M12 1v22", "M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"
]} />;

export const IconSearch = (p: IconProps) => <Im {...p} paths={[
  "M11 3a8 8 0 100 16 8 8 0 000-16z", "M21 21l-4.35-4.35"
]} />;

export const IconBulb = (p: IconProps) => <Im {...p} paths={[
  "M9 18h6", "M10 22h4",
  "M12 2a7 7 0 00-4 12.7V17a1 1 0 001 1h6a1 1 0 001-1v-2.3A7 7 0 0012 2z"
]} />;

export const IconGear = (p: IconProps) => <Im {...p} paths={[
  "M12 15a3 3 0 100-6 3 3 0 000 6z",
  "M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"
]} />;

export const IconChart = (p: IconProps) => <Im {...p} paths={[
  "M22 12h-4l-3 9L9 3l-3 9H2"
]} />;

export const IconShield = (p: IconProps) => <I {...p}
  d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />;

export const IconPen = (p: IconProps) => <Im {...p} paths={[
  "M17 3a2.83 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"
]} />;

export const IconClipboard = (p: IconProps) => <Im {...p} paths={[
  "M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2",
  "M15 2H9a1 1 0 00-1 1v2a1 1 0 001 1h6a1 1 0 001-1V3a1 1 0 00-1-1z"
]} />;

export const IconFlask = (p: IconProps) => <Im {...p} paths={[
  "M9 3h6", "M10 3v5.2a2 2 0 01-.6 1.4L4 15h16l-5.4-5.4a2 2 0 01-.6-1.4V3",
  "M8.5 14.5L6 22h12l-2.5-7.5"
]} />;

export const IconUsers = (p: IconProps) => <Im {...p} paths={[
  "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2",
  "M9 7a4 4 0 100 8 4 4 0 000-8z",
  "M23 21v-2a4 4 0 00-3-3.87",
  "M16 3.13a4 4 0 010 7.75"
]} />;

export const IconBell = (p: IconProps) => <Im {...p} paths={[
  "M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9",
  "M13.73 21a2 2 0 01-3.46 0"
]} />;

export const IconFile = (p: IconProps) => <Im {...p} paths={[
  "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z",
  "M14 2v6h6", "M16 13H8", "M16 17H8", "M10 9H8"
]} />;

export const IconBox = (p: IconProps) => <Im {...p} paths={[
  "M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z",
  "M3.27 6.96L12 12.01l8.73-5.05", "M12 22.08V12"
]} />;

export const IconPlay = (p: IconProps) => <I {...p}
  d="M5 3l14 9-14 9V3z" />;

export const IconSparkle = (p: IconProps) => <Im {...p} paths={[
  "M12 2l2.09 6.26L20 10l-5.91 1.74L12 18l-2.09-6.26L4 10l5.91-1.74L12 2z",
  "M19 15l1.04 3.13L23 19l-2.96.87L19 23l-1.04-3.13L15 19l2.96-.87L19 15z"
]} />;

export const IconBuilding = (p: IconProps) => <Im {...p} paths={[
  "M3 21h18", "M5 21V7l8-4v18", "M19 21V11l-6-4",
  "M9 9h1", "M9 13h1", "M9 17h1"
]} />;

// ── Action Icons (for User Management table) ──

export const IconEye = (p: IconProps) => <Im {...p} paths={[
  "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z",
  "M12 9a3 3 0 100 6 3 3 0 000-6z"
]} />;

export const IconEdit = (p: IconProps) => <Im {...p} paths={[
  "M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7",
  "M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z"
]} />;

export const IconKey = (p: IconProps) => <Im {...p} paths={[
  "M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.78 7.78 5.5 5.5 0 017.78-7.78zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"
]} />;

export const IconBan = (p: IconProps) => <Im {...p} paths={[
  "M12 2a10 10 0 100 20 10 10 0 000-20z",
  "M4.93 4.93l14.14 14.14"
]} />;

export const IconCheck = (p: IconProps) => <Im {...p} paths={[
  "M22 11.08V12a10 10 0 11-5.93-9.14",
  "M22 4L12 14.01l-3-3"
]} />;

export const IconRefresh = (p: IconProps) => <Im {...p} paths={[
  "M23 4v6h-6", "M1 20v-6h6",
  "M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"
]} />;

export const IconPlus = (p: IconProps) => <Im {...p} paths={[
  "M12 5v14", "M5 12h14"
]} />;

export const IconX = (p: IconProps) => <Im {...p} paths={[
  "M18 6L6 18", "M6 6l12 12"
]} />;

export const IconWarn = (p: IconProps) => <Im {...p} paths={[
  "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z",
  "M12 9v4", "M12 17h.01"
]} />;

export const IconArrowLoop = (p: IconProps) => <Im {...p} paths={[
  "M21 2v6h-6", "M3 12a9 9 0 0115-6.7L21 8",
  "M3 22v-6h6", "M21 12a9 9 0 01-15 6.7L3 16"
]} />;

export const IconFilter = (p: IconProps) => <I {...p}
  d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" />;

export const IconTrash = (p: IconProps) => <Im {...p} paths={[
  "M3 6h18", "M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2",
  "M10 11v6", "M14 11v6"
]} />;

export const IconActivity = (p: IconProps) => <Im {...p} paths={[
  "M22 12h-4l-3 9L9 3l-3 9H2"
]} />;

export const IconClock = (p: IconProps) => <Im {...p} paths={[
  "M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z",
  "M12 6v6l4 2"
]} />;
