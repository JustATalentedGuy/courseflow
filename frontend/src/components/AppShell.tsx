import {
  BookOpen,
  Gauge,
  LogOut,
  Menu,
  Plus,
  Search,
  Settings,
  Sparkles,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const links = [
  { to: "/dashboard", label: "Dashboard", icon: Gauge },
  { to: "/courses", label: "Courses", icon: BookOpen },
  { to: "/search", label: "Search", icon: Search },
  { to: "/review", label: "Review", icon: Sparkles },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function AppShell() {
  const [open, setOpen] = useState(false);
  const { signOut } = useAuth();

  return (
    <div className="min-h-screen bg-[#f6f7fb] text-slate-900">
      <button className="fixed left-4 top-4 z-40 rounded-xl bg-slate-950 p-2 text-white lg:hidden" onClick={() => setOpen(true)} aria-label="Open navigation">
        <Menu />
      </button>
      {open ? <button className="fixed inset-0 z-40 bg-slate-950/40 lg:hidden" onClick={() => setOpen(false)} aria-label="Close navigation overlay" /> : null}
      <aside className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-slate-950 px-4 py-5 text-white transition-transform lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex items-center justify-between px-2">
          <NavLink to="/dashboard" className="flex items-center gap-3" onClick={() => setOpen(false)}>
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-blue-500"><BookOpen className="h-5 w-5" /></span>
            <span className="text-xl font-bold tracking-tight">CourseFlow</span>
          </NavLink>
          <button className="lg:hidden" onClick={() => setOpen(false)} aria-label="Close navigation"><X /></button>
        </div>
        <NavLink to="/courses/new" onClick={() => setOpen(false)} className="mt-8 flex items-center justify-center gap-2 rounded-xl bg-blue-500 px-4 py-3 text-sm font-semibold hover:bg-blue-400">
          <Plus className="h-4 w-4" /> Add course
        </NavLink>
        <nav className="mt-6 space-y-1">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setOpen(false)}
              className={({ isActive }) => `flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium ${isActive ? "bg-white/12 text-white" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}
            >
              <Icon className="h-4 w-4" /> {label}
            </NavLink>
          ))}
        </nav>
        <button onClick={signOut} className="mt-auto flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium text-slate-400 hover:bg-white/5 hover:text-white">
          <LogOut className="h-4 w-4" /> Sign out
        </button>
      </aside>
      <main className="min-h-screen lg:pl-64">
        <div className="mx-auto max-w-7xl px-5 py-8 pt-20 sm:px-8 lg:py-10 lg:pt-10">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
