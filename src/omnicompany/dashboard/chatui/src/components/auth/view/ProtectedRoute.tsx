import type { ReactNode } from 'react';
import { useAuth } from '../context/AuthContext';
import Onboarding from '../../onboarding/view/Onboarding';
import AuthLoadingScreen from './AuthLoadingScreen';

type ProtectedRouteProps = {
  children: ReactNode;
};

// [OMNI] 登录已剥离: 单用户平台模式, 不存在 OSS 登录/setup 分支。
// 只保留 loading 占位 + 首次 onboarding(已完成则直接进主界面)。
export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { isLoading, hasCompletedOnboarding, refreshOnboardingStatus } = useAuth();

  if (isLoading) {
    return <AuthLoadingScreen />;
  }

  if (!hasCompletedOnboarding) {
    return <Onboarding onComplete={refreshOnboardingStatus} />;
  }

  return <>{children}</>;
}
