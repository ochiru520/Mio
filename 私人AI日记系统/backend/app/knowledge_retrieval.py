"""Optional local OpenAI embeddings and reranking. Never downloads models."""
import hashlib
import ipaddress
import json
import math
from urllib.parse import urlsplit

import httpx
from .repositories import knowledge_service_repository as repository

DEFAULTS = {'enabled': False, 'base_url': 'http://127.0.0.1:1234/v1',
            'embedding_model': '', 'rerank_model': '', 'candidate_limit': 500}


def options(changes=None):
    with repository.transaction() as conn:
        conn.initialize_retrieval()
        row = conn.retrieval_options()
        current = {**DEFAULTS, **(json.loads(row[0]) if row else {})}
        if changes is not None:
            unknown = set(changes) - set(DEFAULTS)
            if unknown:
                raise ValueError('无效检索设置。')
            current.update(changes)
            parsed = urlsplit(current['base_url'])
            host = parsed.hostname or ''
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == 'localhost'
            if parsed.scheme not in {'http', 'https'} or not local or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('可选语义检索仅支持本机服务地址。')
            current['candidate_limit'] = max(1, min(int(current['candidate_limit']), 1000))
            current['base_url'] = current['base_url'].rstrip('/')
            if current['enabled'] and not current['embedding_model'].strip():
                raise ValueError('启用语义检索前请填写已安装的本地Embedding模型。')
            conn.save_retrieval_options(json.dumps(current))
    return current


def _vectors(client, config, texts):
    response = client.post(config['base_url'] + '/embeddings', json={'model': config['embedding_model'], 'input': texts})
    response.raise_for_status()
    data = sorted(response.json()['data'], key=lambda item: item['index'])
    vectors = [[float(value) for value in item['embedding']] for item in data]
    if len(vectors) != len(texts) or not vectors or not vectors[0] or any(not all(math.isfinite(v) for v in vector) for vector in vectors):
        raise ValueError('本地Embedding返回数据无效。')
    return vectors


def _cosine(left, right):
    if len(left) != len(right):
        raise ValueError('Embedding维度不一致。')
    denominator = math.sqrt(sum(v*v for v in left) * sum(v*v for v in right))
    return sum(a*b for a, b in zip(left, right)) / denominator if denominator else 0


def augment(project_id, query, lexical_rows, limit):
    config = options()
    details = {'mode': 'full_text', 'semantic_enabled': bool(config['enabled']), 'reranked': False,
               'fallback': '', 'candidate_limit': config['candidate_limit']}
    if not config['enabled']:
        return lexical_rows, details
    # Authorization/freshness is checked again by knowledge_service before returning evidence.
    with repository.read() as conn:
        candidates = conn.semantic_chunks(project_id, config['candidate_limit'])
    try:
        from . import agent_file_service as files
        authorized = []
        for row in candidates:
            try:
                files.resolve_read(row['path'])
                authorized.append(dict(row))
            except (ValueError, OSError):
                continue
        signature = hashlib.sha256((config['base_url'] + '\n' + config['embedding_model']).encode()).hexdigest()
        vectors, missing = {}, []
        with repository.read() as conn:
            for row in authorized:
                digest = hashlib.sha256(row['content'].encode()).hexdigest()
                saved = conn.vector(row['id'], signature, digest)
                if saved:
                    vectors[row['id']] = json.loads(saved[0])
                else:
                    missing.append((row, digest))
        with httpx.Client(timeout=12, trust_env=False) as client:
            query_vector = _vectors(client, config, [query])[0]
            for start in range(0, len(missing), 32):
                batch = missing[start:start+32]
                encoded = _vectors(client, config, [row['content'] for row, _ in batch])
                saves = []
                for (row, digest), vector in zip(batch, encoded):
                    vectors[row['id']] = vector
                    saves.append((row['id'], signature, digest, json.dumps(vector)))
                with repository.transaction() as conn:
                    conn.save_vectors(saves)
            semantic = sorted(authorized, key=lambda row: _cosine(query_vector, vectors[row['id']]), reverse=True)[:limit*3]
            merged, scores = {}, {}
            for ranking in (lexical_rows, semantic):
                for rank, row in enumerate(ranking):
                    merged[row['id']] = dict(row)
                    scores[row['id']] = scores.get(row['id'], 0) + 1 / (60 + rank + 1)
            rows = [dict(merged[key], score=score) for key, score in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)][:limit*3]
            details['mode'] = 'hybrid'
            details['semantic_candidates'] = len(authorized)
            if config['rerank_model'] and len(rows) > 1:
                try:
                    response = client.post(config['base_url'] + '/rerank', json={'model': config['rerank_model'], 'query': query, 'documents': [row['content'] for row in rows]})
                    response.raise_for_status()
                    rankings = response.json()['results']
                    rank_scores = {int(item['index']): float(item['relevance_score']) for item in rankings}
                    if set(rank_scores) != set(range(len(rows))) or not all(math.isfinite(v) for v in rank_scores.values()):
                        raise ValueError('重排返回索引不完整。')
                    rows = [rows[index] for index in sorted(rank_scores, key=rank_scores.get, reverse=True)]
                    details['reranked'] = True
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    details['fallback'] = '本地重排不可用，已使用混合排序。'
            return rows[:limit], details
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, OverflowError):
        details['fallback'] = '本地语义服务不可用，已退回全文检索。'
        return lexical_rows, details
