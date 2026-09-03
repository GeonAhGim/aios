import type { AnyConstructor } from "../http";

// spec §3.2/§9 PLT-09: `/readyz`는 봉투 미적용 + 성공/저하 모두 몸체가
// ReadinessReport다. 판정·파싱은 readiness.ts의 parseReadiness가 전담하므로
// 여기서는 raw 몸체만 그대로 돌려준다.
export function withPlatform<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getReadiness(): Promise<unknown> {
      return this.fetchRaw("/readyz");
    }
  };
}
