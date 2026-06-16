# 知识库与检索节点方案

## 1. 目标边界

当前项目已经具备知识库入库、chunk 切分、dense embedding、BM25-like sparse vector、Qdrant hybrid retrieval、rerank、workflow retrieval node、Agent 上下文注入等基础能力。

本方案的目标是在现有链路上增加知识图谱与 Graph RAG 能力。图检索链路必须并行于现有 dense/sparse/hybrid 向量检索链路，而不是替代原链路。

一期不是轻量前置版本，而是完成从知识图谱构建到图检索使用的完整闭环：

```text
数据清洗/格式化
-> LLM 生成本体论建议
-> 用户校验/手动配置/保存本体论
-> LLM/规则抽取实体与关系
-> 实体链接与消歧
-> 三元组去重合并
-> 写入 Neo4j
-> 检索节点并行使用图检索结果
-> 图证据与向量证据一起进入 Agent 上下文
```

一期不做社区摘要，不做 NER/RE 微调，不接本地小模型。但是抽取器接口必须预留，后续可以替换为微调模型或本地模型。

## 2. 现有知识库链路

现有知识库主数据在 Postgres：

```text
KnowledgeBase
-> KnowledgeDocument
-> KnowledgeChunk
```

现有入库链路：

```text
创建 KnowledgeBase
-> 固化 embedding provider/model/dimension 配置
-> 创建 Qdrant collection
-> 上传 KnowledgeDocument
-> 解析文档为 elements
-> elements 切分为 chunks
-> 可选 parent-child chunk
-> 为可检索 chunk 生成 dense embedding
-> 为可检索 chunk 生成 BM25-like sparse vector
-> dense/sparse upsert 到 Qdrant
-> 文档状态 ready
```

现有检索链路：

```text
retrieval node
-> Query Enhancement，可选 rewrite/hyde/multi_query
-> dense search
-> sparse search
-> dense/sparse RRF 融合
-> 多知识库 RRF 融合
-> parent-child 展开
-> rerank，可选
-> 返回 chunks + retrieval metadata
-> AgentInvocation.retrieved_chunks
-> build_agent_context()
```

Graph RAG 必须复用这条主链路的输出契约，避免破坏 workflow、SSE、run step、Agent adapter。

## 3. 一期知识图谱构建流程

### 3.1 数据清洗与格式化

一期复用现有文档解析与 chunk 切分结果。图谱抽取不直接面向原始文件，而是面向结构化后的 chunk。

输入：

```text
KnowledgeBase.name
KnowledgeBase.description
KnowledgeDocument.filename
KnowledgeDocument.metadata_json
KnowledgeChunk.content
KnowledgeChunk.metadata_json
```

优势：

```text
1. 复用已有 parser、chunk、文档状态和错误处理。
2. 抽取结果天然能回溯到 chunk_id。
3. 图检索最终可以回 Postgres 取原文证据。
```

### 3.2 本体论生成与确认

一期不内置具体领域模板。本体论由 LLM 基于知识库上下文假设生成，再交给用户校验和保存。

生成输入：

```text
KnowledgeBase.name
KnowledgeBase.description
document filenames
文档抽样内容
文件类型
chunk section/page/table/code metadata
```

生成输出：

```json
{
  "entity_types": [
    {
      "name": "Law",
      "description": "法律法规、规章、规范性文件",
      "examples": ["中华人民共和国民法典"]
    }
  ],
  "relation_types": [
    {
      "name": "references",
      "subject_types": ["Law", "Clause"],
      "object_types": ["Law", "Clause"],
      "description": "条款引用另一个法规或条款"
    }
  ]
}
```

前端需要提供 ontology proposal 的确认界面：

```text
1. 查看 LLM 建议的实体类型和关系类型。
2. 删除不合理类型。
3. 修改类型名称、描述、约束。
4. 手动新增实体类型和关系类型。
5. 保存为 confirmed ontology。
```

后端建议新增 `KnowledgeOntology`：

```text
id
knowledge_base_id
status: draft | confirmed
entity_types
relation_types
generated_from
user_overrides
created_at
updated_at
```

如果一期想减少表数量，也可以暂存到 `KnowledgeBase.config_json["ontology"]`。但从版本管理、重新抽取、审计和前端草稿体验看，单独表更合理。

### 3.3 V1 图谱抽取器

一期必须实现 V1 抽取器。V1 使用 LLM/规则抽取，但接口要抽象，以便后续替换为微调 NER/RE 或本地模型。

建议接口：

```python
class GraphExtractor:
    def propose_ontology(self, kb_context): ...
    def extract(self, chunk, ontology): ...
    def link_entities(self, extracted_entities, kb_id): ...
    def normalize_relations(self, extracted_relations, linked_entities): ...
```

V1 实现：

```text
chunk content + confirmed ontology
-> LLM 抽取 entities / relations / evidence
-> JSON schema 校验
-> 规则清洗
-> 实体链接与消歧
-> 关系标准化
```

LLM 输出需要约束在用户确认的本体论范围内：

```json
{
  "entities": [
    {
      "name": "Qdrant",
      "type": "Product",
      "aliases": ["qdrant"],
      "description": "向量数据库",
      "confidence": 0.91
    }
  ],
  "relations": [
    {
      "subject": "KnowledgeChunk",
      "predicate": "stored_in",
      "object": "Qdrant",
      "evidence": "upsert dense vector + BM25 sparse vector 到 Qdrant",
      "confidence": 0.86
    }
  ]
}
```

规则清洗至少包括：

```text
1. 丢弃空实体、空关系。
2. 丢弃不在 ontology 中的 entity_type。
3. 丢弃不在 ontology 中的 predicate。
4. 校验 relation subject/object 是否存在。
5. 校验 subject/object 类型是否符合 relation type 约束。
6. 补齐默认 confidence。
7. 保存 evidence text 和 source chunk_id。
```

### 3.4 实体链接与消歧

实体链接与消歧一期必须做。不能只按实体名合并。

候选生成：

```text
1. exact canonical_name match
2. alias match
3. normalized name match
4. same entity_type constraint
5. name embedding similarity，可选
6. shared document/chunk context
7. neighbor relation similarity
```

冲突处理：

```text
1. 高置信 exact/alias/type 命中，直接链接。
2. 多候选相近时，调用 LLM judge。
3. 无可靠候选时，新建实体。
4. 合并实体时保留 alias、source_chunk_ids、description、confidence。
```

实体合并至少考虑：

```text
knowledge_base_id
canonical_name
entity_type
aliases
上下文描述
已有邻居关系
source chunks
```

### 3.5 三元组去重合并

抽取出的三元组必须先经过实体链接，再写入 Neo4j。

去重 key：

```text
knowledge_base_id
subject_entity_id
predicate
object_entity_id
```

合并逻辑：

```text
subject candidate resolved
object candidate resolved
predicate normalized
-> MERGE subject entity
-> MERGE object entity
-> MERGE relation by kb_id + subject + predicate + object
-> append source_chunk_id
-> append source_document_id
-> update confidence
-> update evidence_count
```

## 4. Neo4j 图存储

一期直接使用 Neo4j 作为主图数据库。Postgres 继续保存业务元数据、文档、chunk 正文和 workflow 配置；Neo4j 保存实体关系结构。

### 4.1 Entity 节点

```cypher
(:Entity {
  id,
  knowledge_base_id,
  canonical_name,
  entity_type,
  aliases,
  description,
  confidence,
  source_chunk_ids,
  source_document_ids,
  created_at,
  updated_at
})
```

### 4.2 Relation 边

Neo4j 关系类型建议先统一为 `:RELATION`，真实谓词放在 `predicate` 属性中。这样可以避免用户自定义 predicate 直接变成动态关系类型带来的迁移和查询复杂度。

```cypher
(:Entity)-[:RELATION {
  id,
  knowledge_base_id,
  predicate,
  confidence,
  evidence_count,
  source_chunk_ids,
  source_document_ids,
  evidence_texts,
  created_at,
  updated_at
}]->(:Entity)
```

### 4.3 Chunk 证据策略

一期采用方案 B：Neo4j 不存完整 Chunk 节点，只在实体和关系上保存 `source_chunk_ids`，需要正文时回 Postgres 查询 `KnowledgeChunk`。

原因：

```text
1. 当前 chunk 正文、metadata、document 归属已经在 Postgres。
2. Neo4j 只负责实体关系遍历，职责更清晰。
3. 避免同一份 chunk 正文在两个数据库重复存储。
4. 后续需要图可视化或证据路径展示时，再补 Chunk 节点。
```

后续可扩展为：

```cypher
(:Chunk {id, knowledge_base_id, document_id, content_preview, source_file, page_num})
(:Chunk)-[:MENTIONS]->(:Entity)
(:Chunk)-[:EVIDENCES]->(:RelationEvidence)
```

## 5. 图检索与现有检索并行

图检索链路并行于当前 dense/sparse 链路。

检索节点内部候选通道：

```text
dense_raw
sparse_raw
graph_raw
```

图检索链路：

```text
query
-> query entity linking
-> Neo4j 找相关实体
-> 1-hop / 2-hop 关系扩展
-> 收集 relation.source_chunk_ids 和 entity.source_chunk_ids
-> 回 Postgres 读取 KnowledgeChunk
-> 转成 graph candidate chunks
-> 与 dense/sparse candidates 融合
```

检索节点新增配置：

```json
{
  "graph_enabled": true,
  "graph_top_k": 20,
  "graph_hops": 1,
  "graph_mode": "hybrid_with_vector",
  "graph_weight": 0.8
}
```

`graph_mode` 建议支持：

```text
hybrid_with_vector: graph 与 dense/sparse 并行融合，默认模式。
graph_only: 只使用图检索，便于调试和评估。
disabled: 关闭图检索。
```

融合策略一期沿用 weighted RRF：

```text
dense ranked list
sparse ranked list
graph ranked list
-> weighted RRF
-> dedupe by chunk_id
-> parent-child expansion
-> rerank，可选
-> top_k chunks
```

建议默认权重：

```text
dense weight = 1.0
sparse weight = 0.3
graph weight = 0.8
```

Graph candidate score 可由以下因素构成：

```text
query entity match score
entity disambiguation confidence
relation confidence
path length penalty
evidence_count
chunk mention confidence
```

## 6. 检索返回值与分类预留

一期仍保持现有主合同：

```text
retrieval result -> chunks
WorkflowExecutor.result.retrieved_chunks
AgentInvocation.retrieved_chunks
build_agent_context()
```

图检索结果不要作为另一套完全独立结构传给 Agent。一期要先把图检索命中的实体、关系、路径落回 evidence chunk，再和 dense/sparse 证据一起作为同一批 chunks 传递。

原因：

```text
1. LLM 最终需要可引用、可核查的原文证据。
2. 现有 workflow、SSE、run step、Agent context 都围绕 chunks 工作。
3. 分成 vector_chunks 和 graph_evidence 两套结构会导致上下文预算、rerank、trace、前端展示都要分叉。
```

但是必须预留返回值分类信息。每个 chunk 候选都应包含：

```json
{
  "context_type": "chunk",
  "retrieval_source": "graph",
  "scores": {
    "graph": 0.82
  },
  "graph": {
    "matched_entities": [],
    "matched_relations": [],
    "paths": []
  }
}
```

如果同一个 chunk 同时被向量和图命中，只保留一条记录，合并来源和分数：

```json
{
  "context_type": "chunk",
  "retrieval_source": "hybrid_graph",
  "scores": {
    "dense": 0.71,
    "sparse_qdrant": 2.3,
    "graph": 0.86
  },
  "graph": {
    "matched_entities": ["Qdrant", "KnowledgeChunk"],
    "matched_relations": ["KnowledgeChunk -stored_in-> Qdrant"],
    "paths": []
  }
}
```

retrieval metadata 需要增加图检索追踪信息：

```json
{
  "retrieval_mode": "hybrid+graph+passthrough",
  "dense_retrieved": 20,
  "sparse_retrieved": 18,
  "graph_retrieved": 12,
  "graph_entities": [],
  "graph_relations": [],
  "graph_paths": [],
  "graph_chunk_ids": [],
  "total_returned": 20
}
```

### 6.1 后续 ContextItem 演进

生产环境长期不应把所有检索结果都强行叫 chunk。未来应从：

```text
retrieved_chunks: list[chunk]
```

升级为：

```text
retrieved_context: list[context_item]
```

`context_item` 类型包括：

```text
chunk
graph_path
entity_card
community_summary
document_summary
```

社区摘要尤其不能伪装成普通 chunk。它是图社区压缩信息，不是原文片段，必须带 `community_id`、`supporting_entity_ids`、`supporting_relation_ids`、`supporting_chunk_ids`、`generated_at` 和 `version`。

一期不实现 `ContextItem` 重构，但必须保留 `context_type`、`retrieval_source`、`graph` metadata，避免后续迁移时重写图检索。

## 7. 前端交互

### 7.1 知识库侧

新增知识图谱区域：

```text
1. 生成本体论建议。
2. 查看 ontology draft。
3. 编辑实体类型和关系类型。
4. 保存 confirmed ontology。
5. 触发图谱抽取。
6. 查看图谱构建状态。
7. 查看抽取错误和统计。
```

图谱构建状态建议：

```text
ontology_empty
ontology_draft
ontology_confirmed
extracting
ready
failed
```

### 7.2 检索节点侧

新增图检索配置：

```text
启用图检索
graph_top_k
graph_hops
graph_mode
graph_weight
```

trace 中展示：

```text
dense count
sparse count
graph count
matched entities
matched relations
graph paths
最终返回 chunk 数
```

## 8. 后端模块建议

新增模块：

```text
backend/app/services/knowledge_ontology_service.py
backend/app/services/graph_extractor.py
backend/app/services/graph_entity_linker.py
backend/app/services/neo4j_service.py
backend/app/services/graph_retrieval_service.py
```

职责：

```text
knowledge_ontology_service.py
  ontology proposal、保存、确认、版本管理。

graph_extractor.py
  GraphExtractor 接口与 LLMRuleGraphExtractor V1 实现。

graph_entity_linker.py
  实体候选生成、链接、消歧、合并策略。

neo4j_service.py
  Neo4j 连接、约束创建、entity/relation merge、查询。

graph_retrieval_service.py
  query entity linking、Neo4j path expansion、evidence chunk 回查、graph candidates 构造。
```

现有 `retrieval_service.retrieve_chunks()` 增加 graph channel：

```text
if graph_enabled:
    graph_raw = search_graph_candidates(...)

dense_candidates
sparse_candidates
graph_candidates
-> weighted RRF
```

## 9. 配置与依赖

新增环境变量：

```text
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
GRAPH_EXTRACTION_PROVIDER=
GRAPH_EXTRACTION_MODEL=
GRAPH_EXTRACTION_CREDENTIAL_ID=
GRAPH_EXTRACTION_BASE_URL=
GRAPH_EXTRACTION_TIMEOUT=60
```

如果复用平台模型凭证，则 `GRAPH_EXTRACTION_CREDENTIAL_ID` 可引用已有 `ModelCredential`。不建议直接把图谱抽取绑定到 Agent node 模型配置，抽取是知识库构建能力，不是单次 workflow runtime 能力。

## 10. 一期验收标准

一期完成后应满足：

```text
1. 用户能在知识库中生成 ontology proposal。
2. 用户能编辑并确认 ontology。
3. 系统能基于 confirmed ontology 抽取实体和关系。
4. 系统能执行实体链接与消歧。
5. 系统能去重合并三元组并写入 Neo4j。
6. Neo4j 中能按 knowledge_base_id 查询实体、关系和证据 chunk ids。
7. 检索节点能并行执行 dense/sparse/graph 检索。
8. 图检索结果能回查 Postgres chunk 正文。
9. 最终返回仍是一批 chunks，但包含 context_type、retrieval_source、graph metadata。
10. 同一 chunk 被多个通道命中时不会重复进入 Agent 上下文。
11. retrieval metadata 能展示 graph_retrieved、graph_entities、graph_relations、graph_paths。
12. 可以通过 graph_only 模式单独评估图检索效果。
```

## 11. 后续阶段

二期或后续可以做：

```text
1. NER/RE 标注数据积累。
2. 微调 NER/RE 或替换为本地小模型。
3. Neo4j Chunk 节点与证据路径可视化。
4. 正式引入 ContextItem 返回值类型。
5. 社区发现与社区摘要。
6. GraphRAG hierarchical summary。
7. 图检索评估集与自动化指标。
```

社区摘要后续用于解决宏观归纳和长链路压缩问题：

```text
图构建好
-> 社区发现
-> 每个社区收集实体、关系、代表 chunk
-> 生成 community summary
-> 查询时先召回 summary，再下钻到实体/关系/chunk
```

社区摘要不是一期闭环的必要项。一期优先保证图谱构建、Neo4j 存储和检索节点图证据返回。
