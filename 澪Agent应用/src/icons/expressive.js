/**
 * 沟通、媒体、人物与 AI 类图标。
 * 网格约定见 factory.js：活动区 4–20，圆框半径 8，方框圆角 3。
 */
import { makeIcon, L, Dot, Circle, Rect, Mirror } from './factory.js'

/** 一颗四芒星加一颗小星：Mio 的标记 */
export const Sparkles = makeIcon('Sparkles', [L('M10 5c.6 3.9 2.1 5.4 6 6-3.9.6-5.4 2.1-6 6-.6-3.9-2.1-5.4-6-6 3.9-.6 5.4-2.1 6-6z'), L('M18 3.5v3'), L('M16.5 5h3')])

export const Bot = makeIcon('Bot', [Rect(5, 8, 14, 11, 3), L('M12 8V5'), Dot(12, 4.5), Dot(9.5, 13), Dot(14.5, 13), L('M3 12.5v2'), L('M21 12.5v2')])

/** 左右两个半球，各用一条外轮廓和一道沟 */
const brainHalf = L('M12 5.5A2.6 2.6 0 007.3 6.6 3 3 0 005 10.2a3 3 0 00.4 4.6A2.9 2.9 0 008.6 19 2.6 2.6 0 0012 18.5z')
const brainFold = L('M8 10.5c1.2 0 2 .8 2 2')
export const Brain = makeIcon('Brain', [brainHalf, Mirror(brainHalf), brainFold, Mirror(brainFold)])

const BUBBLE = 'M7 4.5h10a3 3 0 013 3v6.5a3 3 0 01-3 3h-5l-4.5 3.5V17H7a3 3 0 01-3-3V7.5a3 3 0 013-3z'
export const MessageSquareText = makeIcon('MessageSquareText', [L(BUBBLE), L('M8 9.5h8'), L('M8 12.5h5')])
export const Feather = makeIcon('Feather', [L('M19.5 4.5C13 4 8.5 7.5 8 13l-.5 4.5 4.5-.5c5.5-.5 8-6 7.5-12.5z'), L('M4.5 19.5l8-8')])
export const BookOpen = makeIcon('BookOpen', [L('M4 6c2.8-.7 5.5-.2 8 1.5V19c-2.5-1.7-5.2-2.2-8-1.5z'), L('M20 6c-2.8-.7-5.5-.2-8 1.5V19c2.5-1.7 5.2-2.2 8-1.5z')])

export const Image = makeIcon('Image', [Rect(4, 5, 16, 14, 3), Circle(9, 10, 1.5), L('M20 15l-4.5-4.5L7 19')])
export const ImagePlus = makeIcon('ImagePlus', [L('M13 5H7a3 3 0 00-3 3v8a3 3 0 003 3h10a3 3 0 003-3v-3'), Circle(9, 10, 1.5), L('M20 15l-4.5-4.5L7 19'), L('M18 3v6'), L('M15 6h6')])
export const Film = makeIcon('Film', [Rect(4, 5, 16, 14, 3), L('M8.5 5v14'), L('M15.5 5v14'), L('M4 12h4.5'), L('M15.5 12H20')])
export const Video = makeIcon('Video', [Rect(4, 6.5, 11, 11, 3), L('M15 10.5l4.2-2.4a.5.5 0 01.8.4v7a.5.5 0 01-.8.4L15 13.5')])
export const Music = makeIcon('Music', [L('M9 17V6l10-2v11'), Circle(6.5, 17, 2.5), Circle(16.5, 15, 2.5)])
export const Monitor = makeIcon('Monitor', [Rect(4, 4.5, 16, 11.5, 3), L('M9 20h6'), L('M12 16v4')])
/** 手柄：一个胶囊轮廓，左边十字键，右边两颗按键 */
export const Gamepad2 = makeIcon('Gamepad2', [L('M8 7h8a5 5 0 010 10H8A5 5 0 018 7z'), L('M8 10.5v3'), L('M6.5 12h3'), Dot(15, 11), Dot(17, 13)])
/** 小猫：两只耳朵、两只眼睛，用于桌宠 */
export const Cat = makeIcon('Cat', [L('M5 12V5.5l4 3h6l4-3V12a7 7 0 01-14 0z'), Dot(9.5, 13), Dot(14.5, 13), L('M11 16l1 .8 1-.8')])
export const Sun = makeIcon('Sun', [Circle(12, 12, 3.5), L('M12 3.5v2'), L('M12 18.5v2'), L('M3.5 12h2'), L('M18.5 12h2'), L('M6 6l1.4 1.4'), L('M16.6 16.6L18 18'), L('M6 18l1.4-1.4'), L('M16.6 7.4L18 6')])
export const Wifi = makeIcon('Wifi', [L('M4 9.5a11.5 11.5 0 0116 0'), L('M7 13a7.2 7.2 0 0110 0'), L('M10 16.4a3 3 0 014 0'), Dot(12, 19.5)])
export const WifiOff = makeIcon('WifiOff', [L('M4 4l16 16'), L('M4 9.5a11.5 11.5 0 013.5-2.4'), L('M12.5 5.8A11.5 11.5 0 0120 9.5'), L('M7 13a7.2 7.2 0 012.6-1.7'), L('M15 11.5a7.2 7.2 0 012 1.5'), L('M10 16.4a3 3 0 014 0'), Dot(12, 19.5)])
export const Volume2 = makeIcon('Volume2', [L('M4 10v4a1 1 0 001 1h2.5l4 3.5V5.5l-4 3.5H5a1 1 0 00-1 1z'), L('M15 9.5a3.5 3.5 0 010 5'), L('M17.5 7a7 7 0 010 10')])
export const Mic = makeIcon('Mic', [Rect(9, 3.5, 6, 11, 3), L('M5.5 11.5a6.5 6.5 0 0013 0'), L('M12 18v2.5')])
export const MicOff = makeIcon('MicOff', [L('M4 4l16 16'), L('M9 9v2.5a3 3 0 005 2.2'), L('M9.2 5.6A3 3 0 0115 6.5v4.8'), L('M5.5 11.5a6.5 6.5 0 009.8 5.6'), L('M18.5 11.5c0 .8-.1 1.5-.4 2.2'), L('M12 18v2.5')])
/** 听筒：一条弧线加两端的圆头，比写实的话筒轮廓更轻 */
const HANDSET = 'M6.5 4.5h2.2l1.3 3.5-1.8 1.2a9 9 0 006.6 6.6L16 14l3.5 1.3v2.2a2 2 0 01-2 2A13.5 13.5 0 014.5 6.5a2 2 0 012-2z'
export const Phone = makeIcon('Phone', [L(HANDSET)])
export const PhoneCall = makeIcon('PhoneCall', [L(HANDSET), L('M14.5 4.5a5 5 0 015 5'), L('M14.5 7.8a1.7 1.7 0 011.7 1.7')])
export const PhoneOff = makeIcon('PhoneOff', [L(HANDSET), L('M4 4l16 16')])
export const UserRound = makeIcon('UserRound', [Circle(12, 8.5, 3.5), L('M5 20a7 7 0 0114 0')])
/** 扳手：圆头带一个 45° 缺口，杆朝左下 */
export const Wrench = makeIcon('Wrench', [L('M15.28 5.07A4.5 4.5 0 0010.44 11.44L4.94 16.94a1.5 1.5 0 002.12 2.12l5.5-5.5A4.5 4.5 0 0018.93 8.72L16.4 9.2l-1.6-1.6z')])
export const Code2 = makeIcon('Code2', [L('M8 7l-5 5 5 5'), L('M16 7l5 5-5 5'), L('M13.5 5l-3 14')])
export const SquarePen = makeIcon('SquarePen', [L('M12 4H7a3 3 0 00-3 3v10a3 3 0 003 3h10a3 3 0 003-3v-5'), L('M17.5 4.5a1.8 1.8 0 012.5 2.5L13.5 13.5 11 14l.5-2.5z')])
