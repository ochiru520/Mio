/** 沟通、媒体、人物与 AI 类图标。全部只用描边。 */
import { makeIcon, L, Dot, Circle, Rect, Mirror } from './factory.js'

/** 星芒：一颗四芒星加一颗小十字，和主题里的星点同一个语言 */
export const Sparkles = makeIcon('Sparkles', [L('M10 4.5c.7 4.4 2.4 6.1 6.8 6.8-4.4.7-6.1 2.4-6.8 6.8-.7-4.4-2.4-6.1-6.8-6.8C7.6 10.6 9.3 8.9 10 4.5z'), L('M18.2 3.5v3.5'), L('M16.5 5.2h3.5'), L('M18.5 16v3'), L('M17 17.5h3')])

/** Bot：圆润的头、一根天线、两只眼睛 */
export const Bot = makeIcon('Bot', [Rect(4, 9, 16, 10.5, 3), L('M12 9V6'), Circle(12, 4.8, 1.2), Dot(9, 14), Dot(15, 14), L('M2 13.5v2'), L('M22 13.5v2')])

/** Brain：左右对称的两个半球 */
const brainHalf = L('M12 5.5c-.4-1.3-1.5-2.2-2.9-2.2-1.7 0-3 1.3-3 3 0 .3 0 .600.1.8A3.5 3.5 0 004.3 10.2c0 1 .4 1.8 1.1 2.4a3.4 3.4 0 00-1.1 2.5c0 1.7 1.2 3 2.9 3.3.3 1.4 1.4 2.5 3 2.5 1 0 1.4-.4 1.8-.8V5.5z')
export const Brain = makeIcon('Brain', [brainHalf, Mirror(brainHalf)])

export const MessageSquareText = makeIcon('MessageSquareText', [L('M6 4.5h12a2 2 0 012 2v8.2a2 2 0 01-2 2h-6.2L7.5 20v-3.3H6a2 2 0 01-2-2V6.5a2 2 0 012-2z'), L('M8 9.5h8'), L('M8 12.8h5')])
export const Feather = makeIcon('Feather', [L('M19.5 4.5c-5.5-.8-10.5 2-11.5 8L6.8 18l5-1.1c5-1.4 8-6 7.7-12.4z'), L('M4.5 19.5L11 13')])
export const BookOpen = makeIcon('BookOpen', [L('M3.5 6c2.8-.6 5.5-.2 8.5 1.5v12c-3-1.7-5.7-2.1-8.5-1.5z'), L('M20.5 6c-2.8-.6-5.5-.2-8.5 1.5v12c3-1.7 5.7-2.1 8.5-1.5z')])

export const Image = makeIcon('Image', [Rect(4, 5, 16, 14, 3), Circle(9, 10, 1.5), L('M20 15.5l-4.5-4.5L7.5 19')])
export const ImagePlus = makeIcon('ImagePlus', [L('M13.5 5H7a3 3 0 00-3 3v8a3 3 0 003 3h10a3 3 0 003-3v-3'), Circle(9, 10, 1.5), L('M20 15l-4.5-4.5L7.5 19'), L('M18.5 3v6'), L('M15.5 6h6')])
export const Film = makeIcon('Film', [Rect(4, 5, 16, 14, 3), L('M8 5v14'), L('M16 5v14'), L('M4 9.5h4'), L('M4 14.5h4'), L('M16 9.5h4'), L('M16 14.5h4')])
export const Video = makeIcon('Video', [Rect(3.5, 6.5, 12, 11, 3), L('M15.5 10.6l4.5-2.6a.6.6 0 01.9.5v7a.6.6 0 01-.9.5l-4.5-2.6')])
export const Music = makeIcon('Music', [L('M9 17.5V6l10-2v11.5'), Circle(6.5, 17.8, 2.5), Circle(16.5, 15.5, 2.5)])
export const Monitor = makeIcon('Monitor', [Rect(3.5, 4.5, 17, 12, 2.5), L('M9 20h6'), L('M12 16.5V20')])
export const Gamepad2 = makeIcon('Gamepad2', [L('M7 7.5h10a4.2 4.2 0 014.1 3.3l.8 4.2a2.6 2.6 0 01-4.6 2L16 14.8H8l-1.3 2.2a2.6 2.6 0 01-4.6-2l.8-4.2A4.2 4.2 0 017 7.5z'), L('M7.5 10.5v3'), L('M6 12h3'), Dot(15.8, 11), Dot(18, 13)])
export const Sun = makeIcon('Sun', [Circle(12, 12, 4), L('M12 3v2'), L('M12 19v2'), L('M3 12h2'), L('M19 12h2'), L('M5.6 5.6L7 7'), L('M17 17l1.4 1.4'), L('M5.6 18.4L7 17'), L('M17 7l1.4-1.4')])
export const Wifi = makeIcon('Wifi', [L('M3.5 9.5a12.5 12.5 0 0117 0'), L('M6.5 12.8a8.2 8.2 0 0111 0'), L('M9.5 16a3.8 3.8 0 015 0'), Dot(12, 19.2)])
export const WifiOff = makeIcon('WifiOff', [L('M4.5 4.5l15 15'), L('M3.5 9.5a12.5 12.5 0 014.3-2.7'), L('M12.7 6.2a12.5 12.5 0 017.8 3.3'), L('M6.5 12.8a8.2 8.2 0 012.9-1.8'), L('M15.1 11.2a8.2 8.2 0 012.4 1.6'), L('M9.5 16a3.8 3.8 0 015 0'), Dot(12, 19.2)])
export const Volume2 = makeIcon('Volume2', [L('M4.5 9.5h3l4.2-3.6a.7.7 0 011.1.5v11.2a.7.7 0 01-1.1.5L7.5 14.5h-3a.8.8 0 01-.8-.8v-3.4c0-.5.4-.8.8-.8z'), L('M16 9.3a3.8 3.8 0 010 5.4'), L('M18.5 6.8a7.3 7.3 0 010 10.4')])
export const Mic = makeIcon('Mic', [Rect(9, 3.5, 6, 11, 3), L('M5.5 11.5a6.5 6.5 0 0013 0'), L('M12 18v3')])
export const MicOff = makeIcon('MicOff', [L('M4.5 4.5l15 15'), L('M9 9v2.5a3 3 0 004.8 2.4'), L('M9.2 5.7A3 3 0 0115 6.5v4.6'), L('M5.5 11.5a6.5 6.5 0 009.7 5.6M18.5 11.5c0 .8-.1 1.5-.4 2.2'), L('M12 18v3')])
const PHONE = 'M7.5 4l2.3.6c.5.1.8.6.7 1.1l-.7 2.6c-.1.4-.3.7-.6.9l-1 .7a12 12 0 005.6 5.6l.7-1c.2-.3.5-.5.9-.6l2.6-.7c.5-.1 1 .2 1.1.7l.6 2.3c.1.5-.1 1-.5 1.3-1 .8-2.2 1.2-3.5 1C10 18.5 5.7 14.2 4 9c-.2-1.3.2-2.5 1-3.5.3-.4.8-.600 1.3-.500z'
export const Phone = makeIcon('Phone', [L(PHONE)])
export const PhoneCall = makeIcon('PhoneCall', [L(PHONE), L('M15 4a5 5 0 015 5'), L('M15 7.2a1.8 1.8 0 011.8 1.8')])
export const PhoneOff = makeIcon('PhoneOff', [L(PHONE), L('M4 4l16 16')])
export const UserRound = makeIcon('UserRound', [Circle(12, 8.5, 3.5), L('M5 20c.5-3.5 3.4-5.5 7-5.5s6.5 2 7 5.5')])
/** 扳手：竖直画好开口扳手，再整体旋转 45°，开口朝右上 */
export const Wrench = makeIcon('Wrench', [['path', {
  d: 'M10.4 3V6.6H13.6V3H16V8.2c0 1.6-1 2.6-2.4 3V19.4a1.6 1.6 0 01-3.2 0V11.2c-1.4-.4-2.4-1.4-2.4-3V3z',
  transform: 'translate(-1.2 1.2) rotate(45 12 12)',
}]])
export const Code2 = makeIcon('Code2', [L('M8 7.5L3.5 12 8 16.5'), L('M16 7.5l4.5 4.5-4.5 4.5'), L('M13.5 5.5l-3 13')])
export const SquarePen = makeIcon('SquarePen', [L('M12.5 4.5H7a2.5 2.5 0 00-2.5 2.5v10A2.5 2.5 0 007 19.5h10a2.5 2.5 0 002.5-2.5v-5.5'), L('M17.6 3.7a1.8 1.8 0 012.6 2.6l-6.2 6.2-3.4.8.8-3.4z')])
