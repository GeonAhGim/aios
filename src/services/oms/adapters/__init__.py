"""OMS 어댑터(L4 명세 §2-C) — `ports/repository.py` Protocol의 asyncpg 구현.

I/O는 이 패키지에만 있다. `conn`은 항상 호출자가 이미 연 트랜잭션이고,
어댑터는 자신의 커넥션/트랜잭션을 새로 열지 않는다(105번 §5.1).
"""
