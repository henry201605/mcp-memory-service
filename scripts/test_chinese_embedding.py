"""
测试当前 embedding 模型 (all-MiniLM-L6-v2) 对中文的支持情况。

测试维度：
1. 中文同义句匹配 —— 同一个意思不同表达，相似度应该高
2. 中文不相关句区分 —— 不同话题，相似度应该低
3. 中英跨语言匹配 —— 中文查询匹配英文记忆
4. 实际使用场景 —— 模拟你的 AI 问答记忆检索

用法: python scripts/test_chinese_embedding.py
"""

import numpy as np
import sys
import os

# 添加项目 src 到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def load_model():
    """尝试加载项目的 embedding 模型"""
    # 优先用 ONNX（项目默认）
    try:
        from mcp_memory_service.embeddings.onnx_embeddings import get_onnx_embedding_model
        model = get_onnx_embedding_model("all-MiniLM-L6-v2")
        if model:
            print("✅ 使用 ONNX 模型: all-MiniLM-L6-v2")
            return model
    except Exception as e:
        print(f"ONNX 加载失败: {e}")

    # 回退到 sentence-transformers
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        print("✅ 使用 SentenceTransformer 模型: all-MiniLM-L6-v2")
        return model
    except Exception as e:
        print(f"SentenceTransformer 加载失败: {e}")

    print("❌ 无法加载任何 embedding 模型")
    sys.exit(1)


def encode(model, texts):
    """统一的 encode 接口"""
    embeddings = model.encode(texts, convert_to_numpy=True)
    return embeddings


def run_test(model, test_name, query, candidates, expected_best_idx=0):
    """运行单个测试"""
    print(f"\n{'='*60}")
    print(f"📝 {test_name}")
    print(f"{'='*60}")
    print(f"查询: {query}")
    print()

    all_texts = [query] + candidates
    embeddings = encode(model, all_texts)
    query_emb = embeddings[0]

    results = []
    for i, (cand, emb) in enumerate(zip(candidates, embeddings[1:])):
        sim = cosine_similarity(query_emb, emb)
        results.append((sim, i, cand))

    # 按相似度降序排列
    results.sort(reverse=True)

    for rank, (sim, idx, cand) in enumerate(results):
        marker = "🎯" if idx == expected_best_idx else "  "
        bar = "█" * int(sim * 40) + "░" * (40 - int(sim * 40))
        print(f"  {marker} [{sim:.4f}] {bar} {cand}")

    actual_best_idx = results[0][1]
    if actual_best_idx == expected_best_idx:
        print(f"\n  ✅ 正确! 最相关的结果排在第一位")
    else:
        print(f"\n  ❌ 错误! 期望第 {expected_best_idx+1} 条排第一，实际第 {actual_best_idx+1} 条排第一")

    return actual_best_idx == expected_best_idx


def main():
    print("🔍 测试 all-MiniLM-L6-v2 对中文的支持情况\n")
    model = load_model()

    passed = 0
    total = 0

    # ============================================================
    # 测试 1: 中文同义句匹配
    # ============================================================
    total += 1
    if run_test(model,
        "测试 1: 中文同义句匹配",
        query="K8s 部署遇到了问题",
        candidates=[
            "Kubernetes 上线时碰到了故障",       # 同义 ← 应该最高
            "Docker 镜像构建很顺利",             # 不相关
            "Python 依赖安装报错了",             # 弱相关
        ],
        expected_best_idx=0
    ):
        passed += 1

    # ============================================================
    # 测试 2: 中文语义区分
    # ============================================================
    total += 1
    if run_test(model,
        "测试 2: 中文语义区分 —— 能否区分相似但不同的概念",
        query="数据库连接超时怎么解决",
        candidates=[
            "MySQL 连接池耗尽导致超时，需要增大 max_connections",  # 最相关
            "Redis 缓存过期策略配置",                              # 弱相关
            "前端页面加载速度优化",                                 # 不相关
        ],
        expected_best_idx=0
    ):
        passed += 1

    # ============================================================
    # 测试 3: 中英跨语言匹配
    # ============================================================
    total += 1
    if run_test(model,
        "测试 3: 中英跨语言 —— 中文查询能否匹配英文记忆",
        query="如何配置环境变量",
        candidates=[
            "Set environment variables in .env file",    # 英文对应 ← 应该最高
            "Python 虚拟环境的创建方法",                   # 中文但不同话题
            "Docker compose 网络配置",                     # 不相关
        ],
        expected_best_idx=0
    ):
        passed += 1

    # ============================================================
    # 测试 4: 专有名词 / 配置项检索
    # ============================================================
    total += 1
    if run_test(model,
        "测试 4: 专有名词检索 —— 搜配置项名称",
        query="MCP_SSE_HOST 配置",
        candidates=[
            "MCP_SSE_HOST 必须设为 0.0.0.0，否则 K8s 探针失败",  # 精确匹配
            "K8s 部署时首次启动需下载 embedding 模型容易超时",      # 语义相关
            "SSE 心跳间隔建议设为 30 秒",                         # 弱相关
        ],
        expected_best_idx=0
    ):
        passed += 1

    # ============================================================
    # 测试 5: 实际场景 —— 中文 AI 问答记忆检索
    # ============================================================
    total += 1
    if run_test(model,
        "测试 5: 实际场景 —— 中文问答记忆检索",
        query="之前讨论过向量维度选多少合适",
        candidates=[
            "embedding 维度选型：384 维对短文本够用，768 维是通用甜蜜点",  # 最相关
            "Milvus 后端支持 BM25 混合检索提升召回率",                    # 弱相关
            "项目从 ChromaDB 迁移到 SQLite-vec 镜像缩小 68%",            # 不相关
        ],
        expected_best_idx=0
    ):
        passed += 1

    # ============================================================
    # 测试 6: 纯中文近义词区分
    # ============================================================
    total += 1
    if run_test(model,
        "测试 6: 纯中文近义词 —— 能否区分'部署'和'开发'",
        query="生产环境部署方案",
        candidates=[
            "线上服务器的发布流程和运维策略",    # 同义
            "本地开发环境搭建指南",              # 近义但不同
            "代码审查规范和流程",                # 不相关
        ],
        expected_best_idx=0
    ):
        passed += 1

    # ============================================================
    # 测试 7: 中文否定语义
    # ============================================================
    total += 1
    if run_test(model,
        "测试 7: 否定语义 —— 能否区分'能'和'不能'",
        query="这个方案不可行",
        candidates=[
            "该方案存在严重缺陷无法采用",    # 同义（否定）
            "这个方案效果很好值得推广",       # 反义（肯定）
            "天气预报说明天会下雨",           # 不相关
        ],
        expected_best_idx=0
    ):
        passed += 1

    # ============================================================
    # 汇总
    # ============================================================
    print(f"\n{'='*60}")
    print(f"📊 测试结果汇总: {passed}/{total} 通过")
    print(f"{'='*60}")

    if passed == total:
        print("🎉 全部通过! 当前模型对中文支持良好")
    elif passed >= total * 0.7:
        print("⚠️  大部分通过，中文基本可用但有短板")
    else:
        print("❌ 多数失败，建议考虑换用中文友好的模型")


if __name__ == "__main__":
    main()
