export async function selectLocalPath(kind = 'directory') {
  const picker = window.pywebview?.api?.select_agent_path
  if (!picker) throw new Error('请在 Mio 桌面窗口中选择本机文件夹；浏览器中可以使用文件导入或手动填写路径。')
  const result = await picker(kind)
  if (result?.canceled) return null
  if (!result?.ok) throw new Error(result?.error || '无法打开文件选择窗口。')
  return result
}
