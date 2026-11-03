/**
 * 操作类图标：勾、叉、加减、箭头、刷新、搜索、保存、上传下载……
 * 网格约定见 factory.js：活动区 4–20，圆框半径 8，方框圆角 3，箭头两臂 6。
 */
import { makeIcon, L, Dot, Circle, Rect, Mirror } from './factory.js'

export const Check = makeIcon('Check', [L('M5 12.5l4.5 4.5 9.5-10')])
export const X = makeIcon('X', [L('M7 7l10 10'), L('M17 7L7 17')])
export const Plus = makeIcon('Plus', [L('M12 5v14'), L('M5 12h14')])
export const Minus = makeIcon('Minus', [L('M5 12h14')])
export const Square = makeIcon('Square', [Rect(4, 4, 16, 16, 3)])
export const Circle_ = makeIcon('Circle', [Circle(12, 12, 8)])

export const CheckCircle2 = makeIcon('CheckCircle2', [Circle(12, 12, 8), L('M8.5 12.5l2.5 2.5 4.5-5')])
export const CircleAlert = makeIcon('CircleAlert', [Circle(12, 12, 8), L('M12 8v4.5'), Dot(12, 16)])
export const AlertCircle = CircleAlert
export const AlertTriangle = makeIcon('AlertTriangle', [L('M10.3 5a2 2 0 013.4 0l6.5 11.5a2 2 0 01-1.7 3H5.5a2 2 0 01-1.7-3z'), L('M12 9.5v4'), Dot(12, 16.5)])
export const CircleHelp = makeIcon('CircleHelp', [Circle(12, 12, 8), L('M9.7 9.7a2.4 2.4 0 014.6.9c0 1.6-2.3 2-2.3 3.3'), Dot(12, 16.5)])
export const Info = makeIcon('Info', [Circle(12, 12, 8), L('M12 11.5V16'), Dot(12, 8.3)])

export const ArrowLeft = makeIcon('ArrowLeft', [L('M19 12H5'), L('M11 6l-6 6 6 6')])
export const ArrowRight = makeIcon('ArrowRight', [L('M5 12h14'), L('M13 6l6 6-6 6')])
export const ArrowUp = makeIcon('ArrowUp', [L('M12 19V5'), L('M6 11l6-6 6 6')])
export const ArrowUpRight = makeIcon('ArrowUpRight', [L('M7 17L17 7'), L('M8 7h9v9')])
export const ArrowDownToLine = makeIcon('ArrowDownToLine', [L('M12 4v11'), L('M7 10l5 5 5-5'), L('M5 20h14')])
export const ChevronDown = makeIcon('ChevronDown', [L('M6 9l6 6 6-6')])
export const ChevronLeft = makeIcon('ChevronLeft', [L('M15 6l-6 6 6 6')])
export const ChevronRight = makeIcon('ChevronRight', [L('M9 6l6 6-6 6')])
export const MoreHorizontal = makeIcon('MoreHorizontal', [Dot(6, 12), Dot(12, 12), Dot(18, 12)])
export const ExternalLink = makeIcon('ExternalLink', [L('M14 4h6v6'), L('M20 4l-8 8'), L('M18 14v3a3 3 0 01-3 3H7a3 3 0 01-3-3V9a3 3 0 013-3h3')])
export const LogIn = makeIcon('LogIn', [L('M14 4h3a3 3 0 013 3v10a3 3 0 01-3 3h-3'), L('M4 12h11'), L('M11 8l4 4-4 4')])

export const RefreshCw = makeIcon('RefreshCw', [L('M20 12a8 8 0 01-14 5.3'), L('M4 12a8 8 0 0114-5.3'), L('M18 3v3.7h-3.7'), L('M6 21v-3.7h3.7')])
const ROTATE = [L('M20 12a8 8 0 11-2-5.3'), L('M18 3v3.7h-3.7')]
export const RotateCw = makeIcon('RotateCw', ROTATE)
export const RotateCcw = makeIcon('RotateCcw', ROTATE.map(Mirror))
export const LoaderCircle = makeIcon('LoaderCircle', [L('M12 4a8 8 0 108 8')])
export const History = makeIcon('History', [L('M4 12a8 8 0 108-8 8.3 8.3 0 00-6 2.7L4 9'), L('M4 4.5V9h4.5'), L('M12 8v4l3 2')])

export const Search = makeIcon('Search', [Circle(11, 11, 6.5), L('M16 16l4 4')])
export const Eye = makeIcon('Eye', [L('M3 12c2-4 5-6 9-6s7 2 9 6c-2 4-5 6-9 6s-7-2-9-6z'), Circle(12, 12, 2.5)])
export const Eraser = makeIcon('Eraser', [L('M9 20l-4.6-4.6a1.5 1.5 0 010-2.1L13 4.7a1.5 1.5 0 012.1 0l4.2 4.2a1.5 1.5 0 010 2.1L10.5 20z'), L('M11 20h9'), L('M8.5 9l6.5 6.5')])
export const Trash2 = makeIcon('Trash2', [L('M4 7h16'), L('M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2'), L('M6 7l1 11.2A2 2 0 009 20h6a2 2 0 002-1.8L18 7'), L('M10 11v5'), L('M14 11v5')])
export const Copy = makeIcon('Copy', [Rect(9, 9, 11, 11, 3), L('M15 9V7a3 3 0 00-3-3H7a3 3 0 00-3 3v5a3 3 0 003 3h2')])
export const Save = makeIcon('Save', [L('M7 4h8.5L20 8.5V17a3 3 0 01-3 3H7a3 3 0 01-3-3V7a3 3 0 013-3z'), L('M8 20v-5h8v5'), L('M8 4v3h6')])
export const Download = makeIcon('Download', [L('M12 4v11'), L('M7 10l5 5 5-5'), L('M5 20h14')])
export const Upload = makeIcon('Upload', [L('M12 15V4'), L('M7 9l5-5 5 5'), L('M5 20h14')])
export const Send = makeIcon('Send', [L('M20 4l-9 9'), L('M20 4l-5.5 16-3.5-7-7-3.5z')])
export const Paperclip = makeIcon('Paperclip', [L('M19 11l-7.5 7.5a4.2 4.2 0 01-6-6L13.2 4.8a2.8 2.8 0 014 4l-7.7 7.7a1.4 1.4 0 01-2-2l7-7')])
export const Pin = makeIcon('Pin', [L('M8.5 4h7'), L('M10 4v5.5L7 13v1.5h10V13l-3-3.5V4'), L('M12 14.5V20')])
export const PinOff = makeIcon('PinOff', [L('M4 4l16 16'), L('M8.5 4h7'), L('M14 4v5.5l1.6 1.9'), L('M10 7.5v2L7 13v1.5h7.5'), L('M12 14.5V20')])
export const Power = makeIcon('Power', [L('M12 4v8'), L('M16.95 7.05a7 7 0 11-9.9 0')])
export const Maximize2 = makeIcon('Maximize2', [L('M14 4h6v6'), L('M10 20H4v-6'), L('M20 4l-6.5 6.5'), L('M4 20l6.5-6.5')])
export const Star = makeIcon('Star', [L('M12 4l2.4 5 5.4.7-4 3.8 1 5.4-4.8-2.6-4.8 2.6 1-5.4-4-3.8 5.4-.7z')])
export const Heart = makeIcon('Heart', [L('M12 19.5S4.5 15.1 4.5 9.5A4 4 0 0112 7.5a4 4 0 017.5 2c0 5.6-7.5 10-7.5 10z')])
export const Bell = makeIcon('Bell', [L('M6 16v-5a6 6 0 0112 0v5l1.5 2h-15z'), L('M10 21h4')])
export const Pause = makeIcon('Pause', [L('M9 6v12'), L('M15 6v12')])
export const PauseCircle = makeIcon('PauseCircle', [Circle(12, 12, 8), L('M10 9.5v5'), L('M14 9.5v5')])
export const Play = makeIcon('Play', [L('M8 5.5v13a1 1 0 001.5.86l10.4-6.5a1 1 0 000-1.72L9.5 4.64A1 1 0 008 5.5z')])
