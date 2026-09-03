import type { ReactNode } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AdminRoute } from "./components/AdminRoute";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AdminApprovalRequestPage } from "./routes/admin/AdminApprovalRequestPage";
import { MyApprovalRequestsPage } from "./routes/approvals/MyApprovalRequestsPage";
import { AdminHomePage } from "./routes/admin/AdminHomePage";
import { DisputeManagementPage } from "./routes/admin/DisputeManagementPage";
import { PlatformListingPage } from "./routes/admin/PlatformListingPage";
import { UserManagementPage } from "./routes/admin/UserManagementPage";
import { VerificationQueuePage } from "./routes/admin/VerificationQueuePage";
import { WalletTopupsPage } from "./routes/admin/WalletTopupsPage";
import { AlertsPage } from "./routes/alerts/AlertsPage";
import { LoginPage } from "./routes/auth/LoginPage";
import { SignupPage } from "./routes/auth/SignupPage";
import { DashboardPage } from "./routes/dashboard/DashboardPage";
import { DisputeSubmitPage } from "./routes/disputes/DisputeSubmitPage";
import { ExchangeManagementPage } from "./routes/exchanges/ExchangeManagementPage";
import { ExecutionControlPage } from "./routes/executions/ExecutionControlPage";
import { ListingDetailPage } from "./routes/marketplace/ListingDetailPage";
import { MarketplaceBrowsePage } from "./routes/marketplace/MarketplaceBrowsePage";
import { SellStrategyPage } from "./routes/marketplace/SellStrategyPage";
import { MfaSetupPage } from "./routes/onboarding/MfaSetupPage";
import { RiskAssessmentPage } from "./routes/onboarding/RiskAssessmentPage";
import { PortfolioPage } from "./routes/portfolio/PortfolioPage";
import { ReportsPage } from "./routes/reports/ReportsPage";
import { WriteReviewPage } from "./routes/reviews/WriteReviewPage";
import { AccountDeletionPage } from "./routes/settings/AccountDeletionPage";
import { ApprovalSettingsPage } from "./routes/settings/ApprovalSettingsPage";
import { NotificationSettingsPage } from "./routes/settings/NotificationSettingsPage";
import { SessionsPage } from "./routes/settings/SessionsPage";
import { StrategyBuilderPage } from "./routes/strategy-builder/StrategyBuilderPage";
import { SystemStatusPage } from "./routes/system/SystemStatusPage";
import { LedgerHistoryPage } from "./routes/wallet/LedgerHistoryPage";
import { WalletPage } from "./routes/wallet/WalletPage";

function protect(element: ReactNode) {
  return <ProtectedRoute>{element}</ProtectedRoute>;
}

function protectAdmin(element: ReactNode) {
  return (
    <ProtectedRoute>
      <AdminRoute>{element}</AdminRoute>
    </ProtectedRoute>
  );
}

export const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/dashboard" replace /> },
  { path: "/signup", element: <SignupPage /> },
  { path: "/login", element: <LoginPage /> },
  { path: "/onboarding/mfa-setup", element: protect(<MfaSetupPage />) },
  { path: "/onboarding/risk-assessment", element: protect(<RiskAssessmentPage />) },
  { path: "/dashboard", element: protect(<DashboardPage />) },
  { path: "/exchanges", element: protect(<ExchangeManagementPage />) },
  { path: "/strategy-builder", element: protect(<StrategyBuilderPage />) },
  { path: "/marketplace", element: protect(<MarketplaceBrowsePage />) },
  { path: "/marketplace/sell", element: protect(<SellStrategyPage />) },
  { path: "/marketplace/:listingId", element: protect(<ListingDetailPage />) },
  { path: "/executions", element: protect(<ExecutionControlPage />) },
  { path: "/portfolio", element: protect(<PortfolioPage />) },
  { path: "/reports", element: protect(<ReportsPage />) },
  { path: "/wallet", element: protect(<WalletPage />) },
  { path: "/wallet/ledger", element: protect(<LedgerHistoryPage />) },
  { path: "/alerts", element: protect(<AlertsPage />) },
  { path: "/approval-requests", element: protect(<MyApprovalRequestsPage />) },
  { path: "/settings/approval", element: protect(<ApprovalSettingsPage />) },
  { path: "/settings/notifications", element: protect(<NotificationSettingsPage />) },
  { path: "/settings/account", element: protect(<AccountDeletionPage />) },
  { path: "/settings/sessions", element: protect(<SessionsPage />) },
  { path: "/reviews/write/:purchaseId", element: protect(<WriteReviewPage />) },
  { path: "/disputes/submit", element: protect(<DisputeSubmitPage />) },
  { path: "/admin", element: protectAdmin(<AdminHomePage />) },
  { path: "/admin/system-status", element: protectAdmin(<SystemStatusPage />) },
  { path: "/admin/verification-queue", element: protectAdmin(<VerificationQueuePage />) },
  { path: "/admin/disputes", element: protectAdmin(<DisputeManagementPage />) },
  { path: "/admin/users", element: protectAdmin(<UserManagementPage />) },
  { path: "/admin/wallet-topups", element: protectAdmin(<WalletTopupsPage />) },
  {
    path: "/admin/marketplace/platform-listings",
    element: protectAdmin(<PlatformListingPage />),
  },
  {
    path: "/admin/approval-requests",
    element: protectAdmin(<AdminApprovalRequestPage />),
  },
  {
    path: "/admin/approval-requests/:requestId",
    element: protectAdmin(<AdminApprovalRequestPage />),
  },
]);
