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

## 检索研究演练

```sh
python3 -m corpus_gate --db :memory: research-demo --fixture fixtures/research.json
```

`research.*` 操作把一次检索研究冻结在固定搜索索引、文档版本、用途授权与查询集上。编辑按查询族登记 0–3 级相关性，同文转载按 `exact_hash` 去重为一个信息单元；一个查询族的多个同义改写在选参与最终比较中只占一票。候选排序（BM25）的词项统计只取自冻结索引，参数只在独立开发查询族上选择；最终比较集锁定后，补标或调参只能通过 `research.revise` 产生新版本。

`research.report` 按查询族等权汇总：每个查询显示去重后的排名、增益（2 的等级次幂减一）、折损（名次 r 的分母为 log2(r+1)）与 NDCG@k，另给已判断相关文档上的召回与判断覆盖率；未判断文档不计入相关，没有已知相关或理想增益为零时保留不可计算状态。总体差异附按查询族成对重采样的区间与预登记查询类型分组，平分排名按固定规则打破。`research.export` 分两级：数值结果按冻结授权交付，原文片段要求当前授权，来源撤回后片段即被阻断。`search.query` 的默认行为不变。
