import { createBrowserRouter, Navigate } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { LoginPage } from "./routes/auth/LoginPage";
import { SignupPage } from "./routes/auth/SignupPage";
import { DashboardPage } from "./routes/dashboard/DashboardPage";
import { MfaSetupPage } from "./routes/onboarding/MfaSetupPage";
import { RiskAssessmentPage } from "./routes/onboarding/RiskAssessmentPage";

export const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/dashboard" replace /> },
  { path: "/signup", element: <SignupPage /> },
  { path: "/login", element: <LoginPage /> },
  {
    path: "/onboarding/mfa-setup",
    element: (
      <ProtectedRoute>
        <MfaSetupPage />
      </ProtectedRoute>
    ),
  },
  {
    path: "/onboarding/risk-assessment",
    element: (
      <ProtectedRoute>
        <RiskAssessmentPage />
      </ProtectedRoute>
    ),
  },
  {
    path: "/dashboard",
    element: (
      <ProtectedRoute>
        <DashboardPage />
      </ProtectedRoute>
    ),
  },
]);
