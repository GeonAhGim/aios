import { ApiClientBase } from "./http";
import { withAccount } from "./clients/account";
import { withAdmin } from "./clients/admin";
import { withAuth } from "./clients/auth";
import { withExchange } from "./clients/exchange";
import { withExecutions } from "./clients/executions";
import { withMarketplace } from "./clients/marketplace";
import { withNotifications } from "./clients/notifications";
import { withPlatform } from "./clients/platform";
import { withPortfolio } from "./clients/portfolio";
import { withStrategyBuilder } from "./clients/strategyBuilder";

// 도메인별 메서드는 clients/*.ts의 믹스인으로 분리되어 있다(파일당 ≤300줄
// 유지 목적). AiosApiClient는 그 전부를 합성한 단일 클래스로, 공개
// 메서드 표면은 분할 이전과 동일하다.
const ComposedApiClient = withPlatform(
  withAdmin(
    withNotifications(
      withMarketplace(
        withStrategyBuilder(
          withExchange(withExecutions(withPortfolio(withAccount(withAuth(ApiClientBase))))),
        ),
      ),
    ),
  ),
);

export class AiosApiClient extends ComposedApiClient {
  constructor(baseUrl: string, getToken: () => string | null) {
    super(baseUrl, getToken);
  }
}

export { ApiError, buildApiError } from "./http";
