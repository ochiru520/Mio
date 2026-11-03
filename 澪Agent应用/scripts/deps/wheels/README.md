# Anif 离线二进制依赖

`jieba_fast-0.53-cp310-cp310-win_amd64.whl` 由未经修改的 PyPI 0.53 源码包在本机编译，适用于 Windows x64 / CPython 3.10。它是 Mio 的本地候选制品，不是上游发布的官方二进制包；尚未上传。

PyPI 该版本只有含 C 扩展的源码包。随程序携带此 wheel，使用户安装 Anif 时无需自行安装 C 编译器，也不改变现有分词实现。安装前必须校验 `jieba-fast-provenance.json` 记录的 SHA256。

来源、源码及制品哈希、构建命令见 `jieba-fast-provenance.json`。该包声明 MIT 许可，但源码包未附许可正文；从包作者仓库取得的原始许可正文随附于 `jieba-fast-LICENSE.txt`，其来源和内容哈希也记录在 provenance 中。
