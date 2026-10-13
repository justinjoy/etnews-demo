#!/usr/bin/env python3
"""슬라이드 7 'RAG의 기본 구조' 중 검색(Retrieval)을 코드로: 임베딩으로 찾는다.

    문서 -> [임베딩] -> 색인 -> [질문 임베딩 + 코사인 검색] -> 근거

핵심: 검색은 LLM이 아니라 '벡터 유사도'가 한다. 여기서는 그 검색만 보여준다.
임베딩은 키·의존성 없이 동작하도록 아주 단순한 '문자 bigram 빈도 벡터'를
쓴다. 실제 서비스라면 embed() 한 함수만 신경망 임베딩(예: Voyage)으로 바꾸면 된다.
"""
import math
from collections import Counter

# 1) 문서: 검색 가능한 작은 조각들 -----------------------------------------
DOCS = [
    "환불은 구매 후 14일 이내에 가능합니다.",
    "배송은 결제 완료 후 보통 2~3일 걸립니다.",
    "회원 등급은 최근 1년 구매 금액으로 결정됩니다.",
]


# 2) 임베딩: 텍스트 -> 벡터(문자 bigram 빈도). 단순하지만 '진짜 벡터'다. ------
def embed(text: str) -> Counter:
    s = text.replace(" ", "")
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


def cosine(a: Counter, b: Counter) -> float:
    na, nb = math.hypot(*a.values()), math.hypot(*b.values())
    return sum(a[t] * b[t] for t in a) / (na * nb) if na and nb else 0.0


# 색인: 문서 임베딩을 미리 계산해 둔다 (질문마다 다시 만들지 않는다) ----------
INDEX = [(doc, embed(doc)) for doc in DOCS]


# 3) 검색: 질문도 임베딩한 뒤 코사인 유사도가 높은 순으로 정렬한다 ------------
def retrieve(query: str) -> list[tuple[float, str]]:
    q = embed(query)
    return sorted(((cosine(q, vec), doc) for doc, vec in INDEX), reverse=True)


def answer(query: str) -> None:
    ranked = retrieve(query)
    print(f"[질문]   {query}")
    for score, doc in ranked:                       # 검색 과정을 눈으로
        print(f"  유사도 {score:.3f}  {doc}")
    print(f"[검색됨] {ranked[0][1]}")


if __name__ == "__main__":
    answer("환불 배송 회원")
