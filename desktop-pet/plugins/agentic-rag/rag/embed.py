"""文本嵌入：bge-base-zh-v1.5（768维，中文优化）。

直接用底层模块手动实现嵌入 pipeline，绕过 SentenceTransformer.encode
（ST 6.x 的 encode 封装有巨大额外开销，实测比底层慢 40x+）。

流程：tokenize → Transformer forward → Pooling(CLS) → L2 归一化。
按 token 长度分桶提升吞吐；超长文本截断到 MAX_SEQ。
"""

import os
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

MODEL_NAME = "BAAI/bge-base-zh-v1.5"
MAX_SEQ = 512

_model = None
_device = None


def _pick_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def load_model(name=MODEL_NAME):
    global _model, _device
    if _model is not None:
        return _model
    _device = _pick_device()
    import torch
    print(f"[embed] 加载模型 {name}，device={_device}", flush=True)
    from sentence_transformers import SentenceTransformer
    st = SentenceTransformer(name, device=_device)
    st.max_seq_length = MAX_SEQ
    _model = st
    return st


def _encode_tokens(model, tokenized, batch_size):
    """对已 tokenize 的 BatchEncoding 分 batch 前向，返回与输入顺序一致的向量列表。"""
    import torch

    tr = model[0]  # Transformer
    pooler = model[1]  # Pooling (CLS)
    norm = model[2]  # Normalize

    ids = tokenized["input_ids"]
    mask = tokenized["attention_mask"]
    n = ids.shape[0]

    vecs = []
    tr.eval()
    with torch.no_grad():
        for s in range(0, n, batch_size):
            e = min(s + batch_size, n)
            features = {
                "input_ids": ids[s:e].to(_device),
                "attention_mask": mask[s:e].to(_device),
                "token_type_ids": tokenized["token_type_ids"][s:e].to(_device),
            }
            out = tr(features=features)
            pooled = pooler(features=out)
            if norm is not None:
                pooled = norm(pooled)
            vecs.extend(pooled["sentence_embedding"].cpu().tolist())
    return vecs


def embed(texts, batch_size=128):
    """批量嵌入。按 token 长度排序分桶，返回与原顺序一致的向量列表。"""
    model = load_model()
    texts = list(texts)
    if not texts:
        return []

    tok = model.tokenizer
    toks = tok(texts, truncation=True, max_length=MAX_SEQ,
               padding=False, add_special_tokens=True)
    lens = [len(t) for t in toks["input_ids"]]

    order = sorted(range(len(texts)), key=lambda i: lens[i])
    vecs = [None] * len(texts)

    i = 0
    while i < len(order):
        # 取一个桶：预估该桶最长 token，按长度自适应 batch（短文本多塞，长文本少塞）
        bucket = []
        max_len = 0
        while i < len(order):
            idx = order[i]
            l = lens[idx]
            max_len = max(max_len, l)
            if len(bucket) + 1 > max(2, batch_size * (128 / max(max_len, 8))):
                break
            bucket.append(idx)
            i += 1
            if max_len >= 480 and len(bucket) >= 8:
                break
        bucket_toks = tok(
            [texts[j] for j in bucket],
            truncation=True, max_length=MAX_SEQ,
            padding=True, return_tensors="pt",
        )
        out = _encode_tokens(model, bucket_toks, batch_size=len(bucket))
        for j, v in zip(bucket, out):
            vecs[j] = v
    return vecs


def embed_query(text):
    vecs = embed([text])
    return vecs[0]


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    v = embed_query("下界合金锭怎么合成？")
    print("维度:", len(v), "前5维:", v[:5])
