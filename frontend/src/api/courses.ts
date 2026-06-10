import { request } from "./client";
import type {
  CourseCreate,
  CourseDetail,
  CourseResponse,
  CourseStatusResponse,
} from "../types/course";

export function createCourse(payload: CourseCreate, accessToken: string): Promise<CourseResponse> {
  return request<CourseResponse>("/courses", {
    method: "POST",
    accessToken,
    body: JSON.stringify(payload),
  });
}

export function listCourses(accessToken: string): Promise<CourseResponse[]> {
  return request<CourseResponse[]>("/courses", { accessToken });
}

export function getCourse(courseId: string, accessToken: string): Promise<CourseDetail> {
  return request<CourseDetail>(`/courses/${courseId}`, { accessToken });
}

export function getCourseStatus(
  courseId: string,
  accessToken: string,
): Promise<CourseStatusResponse> {
  return request<CourseStatusResponse>(`/courses/${courseId}/status`, { accessToken });
}

export function deleteCourse(courseId: string, accessToken: string): Promise<void> {
  return request<void>(`/courses/${courseId}`, {
    method: "DELETE",
    accessToken,
  });
}
