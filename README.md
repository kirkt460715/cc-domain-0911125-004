# 技术语料接收、审查与检索

文档版本、来源授权、去重簇和审查决定分别留痕。搜索索引基于固定文档集合，命中片段仍受授权约束。

运行需要 Python 3.11 或更高版本，使用标准库与 SQLite，无需第三方包。演练中的机构、人员、交易及读数全部为本地合成数据。

## 构建与测试

```sh
python3 -m compileall -q corpus_gate
python3 -m unittest discover -s tests -v
```

## 本地运行

```sh
python3 -m corpus_gate --db :memory: demo --fixture fixtures/demo.json
python3 -m corpus_gate --help
```

演练使用临时数据库，不会连接远程服务。命令以 JSON 显示业务结果；测试覆盖核心状态、边界输入和错误路径。运行前可用 `python3 --version` 核对解释器版本。
