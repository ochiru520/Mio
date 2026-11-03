/**
 * 对象类图标：文件、文件夹、面板、设置、日历、数据……
 * 网格约定见 factory.js：活动区 4–20，圆框半径 8，方框圆角 3。
 */
import { makeIcon, gearPath, L, Dot, Circle, Rect } from './factory.js'

const PAGE = 'M14 4H8a3 3 0 00-3 3v10a3 3 0 003 3h8a3 3 0 003-3V9z'
const FOLDER = 'M4 17V7a2 2 0 012-2h3.5l2 2.5H18a2 2 0 012 2V17a2 2 0 01-2 2H6a2 2 0 01-2-2z'

export const FileText = makeIcon('FileText', [L(PAGE), L('M14 4v5h5'), L('M9 13h6'), L('M9 16.5h4')])
export const FilePenLine = makeIcon('FilePenLine', [L('M12 4H8a3 3 0 00-3 3v10a3 3 0 003 3h8a3 3 0 003-3v-5'), L('M17.5 4.5a1.8 1.8 0 012.5 2.5L14.5 12.5 12 13l.5-2.5z'), L('M9 16.5h4')])
export const FolderOpen = makeIcon('FolderOpen', [L('M4 17V7a2 2 0 012-2h3.5l2 2.5H17a2 2 0 012 2V11'), L('M4 17l2-5a1.5 1.5 0 011.4-1h11.4a1 1 0 01.9 1.4l-2.2 5.4A2 2 0 0115.7 19H6a2 2 0 01-2-2z')])
export const FolderKey = makeIcon('FolderKey', [L(FOLDER), Circle(12, 12, 1.8), L('M12 13.8V16')])
export const Archive = makeIcon('Archive', [Rect(4, 4.5, 16, 4.5, 1.5), L('M5.5 9v8a3 3 0 003 3h7a3 3 0 003-3V9'), L('M10 13h4')])
export const Database = makeIcon('Database', [L('M5 6.5C5 5.1 8.1 4 12 4s7 1.1 7 2.5S15.9 9 12 9 5 7.9 5 6.5z'), L('M5 6.5v11C5 18.9 8.1 20 12 20s7-1.1 7-2.5v-11'), L('M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5')])
export const Cloud = makeIcon('Cloud', [L('M7.5 19a4 4 0 01-.5-8 5.5 5.5 0 0110.7 1.3A3.4 3.4 0 0117 19z')])
export const Cpu = makeIcon('Cpu', [Rect(6, 6, 12, 12, 3), Rect(9.5, 9.5, 5, 5, 1), L('M10 3v3'), L('M14 3v3'), L('M10 18v3'), L('M14 18v3'), L('M3 10h3'), L('M3 14h3'), L('M18 10h3'), L('M18 14h3')])
export const Globe = makeIcon('Globe', [Circle(12, 12, 8), L('M4 12h16'), L('M12 4c2.2 2.2 3.3 4.9 3.3 8s-1.1 5.8-3.3 8c-2.2-2.2-3.3-4.9-3.3-8S9.8 6.2 12 4z')])
export const Home = makeIcon('Home', [L('M4.5 10.5L12 4l7.5 6.5V18a2 2 0 01-2 2h-11a2 2 0 01-2-2z'), L('M10 20v-5h4v5')])
export const CalendarDays = makeIcon('CalendarDays', [Rect(4, 5.5, 16, 14.5, 3), L('M4 10h16'), L('M8.5 3.5v4'), L('M15.5 3.5v4'), Dot(8.5, 14), Dot(12, 14), Dot(15.5, 14), Dot(8.5, 17), Dot(12, 17)])
export const Clock3 = makeIcon('Clock3', [Circle(12, 12, 8), L('M12 7.5V12h3.5')])

export const Settings = makeIcon('Settings', [L(gearPath()), Circle(12, 12, 2.5)])
export const Settings2 = makeIcon('Settings2', [L('M4 8h8'), L('M18 8h2'), L('M4 16h2'), L('M12 16h8'), Circle(15, 8, 2.5), Circle(9, 16, 2.5)])

const FRAME = Rect(4, 5, 16, 14, 3)
export const PanelLeft = makeIcon('PanelLeft', [FRAME, L('M9.5 5v14')])
export const PanelRight = makeIcon('PanelRight', [FRAME, L('M14.5 5v14')])
export const PanelLeftClose = makeIcon('PanelLeftClose', [FRAME, L('M9.5 5v14'), L('M16 10l-2 2 2 2')])
export const PanelLeftOpen = makeIcon('PanelLeftOpen', [FRAME, L('M9.5 5v14'), L('M14 10l2 2-2 2')])
export const PanelRightClose = makeIcon('PanelRightClose', [FRAME, L('M14.5 5v14'), L('M8 10l2 2-2 2')])
export const PanelRightOpen = makeIcon('PanelRightOpen', [FRAME, L('M14.5 5v14'), L('M10 10l-2 2 2 2')])
export const PanelsTopLeft = makeIcon('PanelsTopLeft', [FRAME, L('M4 10h16'), L('M9.5 10v9')])
export const Grid2X2 = makeIcon('Grid2X2', [Rect(4, 4, 6.5, 6.5, 2), Rect(13.5, 4, 6.5, 6.5, 2), Rect(4, 13.5, 6.5, 6.5, 2), Rect(13.5, 13.5, 6.5, 6.5, 2)])
export const List = makeIcon('List', [L('M9.5 7H20'), L('M9.5 12H20'), L('M9.5 17H20'), Dot(5, 7), Dot(5, 12), Dot(5, 17)])
export const ListChecks = makeIcon('ListChecks', [L('M13 7h7'), L('M13 12h7'), L('M13 17h7'), L('M4.5 7l1.5 1.5 3-3'), L('M4.5 12l1.5 1.5 3-3'), L('M4.5 17l1.5 1.5 3-3')])
export const Workflow = makeIcon('Workflow', [Rect(4, 4, 6.5, 6.5, 2), Rect(13.5, 13.5, 6.5, 6.5, 2), L('M7.25 10.5V14a2 2 0 002 2h4.25')])
export const PackageCheck = makeIcon('PackageCheck', [L('M20 12V8.3a1.5 1.5 0 00-.8-1.3l-6.5-3.6a1.5 1.5 0 00-1.4 0L4.8 7A1.5 1.5 0 004 8.3v7.4a1.5 1.5 0 00.8 1.3l6.5 3.6a1.5 1.5 0 001.4 0'), L('M4.3 7.5L12 12l7.7-4.5'), L('M12 12v8.5'), L('M15.5 18l2 2 3.5-4')])
export const ShieldCheck = makeIcon('ShieldCheck', [L('M12 4l7 2.5v5.3c0 4.2-2.9 7.2-7 8.2-4.1-1-7-4-7-8.2V6.5z'), L('M9 12l2 2 4-4')])
export const KeyRound = makeIcon('KeyRound', [Circle(15, 9, 5), L('M11.5 12.5L4 20'), L('M6.5 17.5L8.5 19.5'), L('M9 15l1.5 1.5'), Dot(16.2, 7.8)])
export const QrCode = makeIcon('QrCode', [Rect(4, 4, 6, 6, 1.5), Rect(14, 4, 6, 6, 1.5), Rect(4, 14, 6, 6, 1.5), L('M14 14h2.5v2.5'), Dot(20, 14), Dot(14, 20), L('M17 20h3v-3')])
export const BarChart3 = makeIcon('BarChart3', [L('M4 4v16h16'), L('M9 16v-4'), L('M13 16V8'), L('M17 16v-6')])
export const ChartNoAxesCombined = makeIcon('ChartNoAxesCombined', [L('M6 20v-5'), L('M12 20v-9'), L('M18 20V8'), L('M5 10l6-4.5 3 2L19 4')])
export const Gauge = makeIcon('Gauge', [L('M4.5 17.5a8 8 0 1115 0'), L('M12 15l3-4.5'), Dot(12, 15)])
export const Activity = makeIcon('Activity', [L('M3.5 12h4l2.5-6.5 4 13 2.5-6.5h4')])
export const CircleDollarSign = makeIcon('CircleDollarSign', [Circle(12, 12, 8), L('M14.6 9.5c-.5-.8-1.4-1.2-2.6-1.2-1.5 0-2.6.7-2.6 1.8s.9 1.5 2.6 1.8 2.6.7 2.6 1.9-1.1 1.9-2.6 1.9c-1.2 0-2.1-.4-2.6-1.2'), L('M12 6.5v1.8'), L('M12 15.7v1.8')])
export const Target = makeIcon('Target', [Circle(12, 12, 8), Circle(12, 12, 4), Dot(12, 12)])
/** 两层叠放的纸面：用于「记忆」——被收好的东西 */
export const Layers = makeIcon('Layers', [L('M12 4l8 4.2-8 4.3-8-4.3z'), L('M4 12.3l8 4.2 8-4.2'), L('M4 16.3l8 4.2 8-4.2')])
