import { lazy, Suspense, type PropsWithChildren } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { AppShell } from "../components/AppShell";
import { LoginPage, RegisterPage } from "./AuthPages";

const DashboardPage = lazy(() =>
  import("./DashboardPage").then((module) => ({ default: module.DashboardPage })),
);
const CoursesPage = lazy(() =>
  import("./CoursePages").then((module) => ({ default: module.CoursesPage })),
);
const NewCoursePage = lazy(() =>
  import("./CoursePages").then((module) => ({ default: module.NewCoursePage })),
);
const CourseDetailPage = lazy(() =>
  import("./CoursePages").then((module) => ({ default: module.CourseDetailPage })),
);
const VideoDetailPage = lazy(() =>
  import("./VideoDetailPage").then((module) => ({ default: module.VideoDetailPage })),
);
const SearchPage = lazy(() =>
  import("./SearchPage").then((module) => ({ default: module.SearchPage })),
);
const ReviewPage = lazy(() =>
  import("./ReviewPage").then((module) => ({ default: module.ReviewPage })),
);
const SettingsPage = lazy(() =>
  import("./SettingsPage").then((module) => ({ default: module.SettingsPage })),
);

function ProtectedRoute({ children }: PropsWithChildren) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? children : <Navigate to="/login" replace />;
}

function HomeRedirect() {
  const { isAuthenticated } = useAuth();
  return <Navigate to={isAuthenticated ? "/dashboard" : "/login"} replace />;
}

export function App() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f6f7fb]" />}>
      <Routes>
        <Route path="/" element={<HomeRedirect />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<ProtectedRoute><AppShell /></ProtectedRoute>}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/courses" element={<CoursesPage />} />
          <Route path="/courses/new" element={<NewCoursePage />} />
          <Route path="/courses/:id" element={<CourseDetailPage />} />
          <Route path="/videos/:id" element={<VideoDetailPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<HomeRedirect />} />
      </Routes>
    </Suspense>
  );
}
