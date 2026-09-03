-- PLT-30 — 환경 부트스트랩용 정적 SQL.
--
-- Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §10 리스크2.
-- src/core/db/roles.py::ensure_roles_sql("aios_app", "aios_migrator")가 생성하는
-- 문장과 동일하다 — 그 함수는 마이그레이션(4a1d0c0de001)이 이미 한 번 실행했지만,
-- 이 파일은 alembic이 붙기 *전* 단계(§16.12-A 순서 1단계, `psql -f`)에서 role이
-- 먼저 존재해야 하는 CI/로컬 부트스트랩 경로를 위해 별도로 둔다. `CREATE ROLE ...
-- IF NOT EXISTS` 동등한 DO 블록이라 마이그레이션과 두 번 실행해도 안전(멱등)하다.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'aios_migrator') THEN
        CREATE ROLE aios_migrator LOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'aios_app') THEN
        CREATE ROLE aios_app LOGIN;
    END IF;
END
$$;

GRANT ALL PRIVILEGES ON SCHEMA public TO aios_migrator;
GRANT USAGE ON SCHEMA public TO aios_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aios_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aios_app;
ALTER DEFAULT PRIVILEGES FOR ROLE aios_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aios_app;
ALTER DEFAULT PRIVILEGES FOR ROLE aios_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO aios_app;
