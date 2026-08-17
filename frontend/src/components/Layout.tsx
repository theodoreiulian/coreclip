import React from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import { GridIcon, PlusIcon, TerminalIcon, PlayIcon, SettingsIcon } from './Icons';

const topNav = [
  { to: '/', label: 'Dashboard', icon: GridIcon, end: true },
  { to: '/logs', label: 'Logs', icon: TerminalIcon, end: false },
];

const bottomNav = [
  { to: '/settings', label: 'Settings', icon: SettingsIcon, end: false },
];

export default function Layout() {
  return (
    <div className="relative flex h-screen overflow-hidden text-[var(--text)]">
      <div className="app-ambient" />

      {/* ── Sidebar rail ─────────────────────────────────────────── */}
      <aside className="relative z-10 flex w-[248px] flex-none flex-col p-4">
        <div className="glass sheen flex h-full flex-col p-4">
          {/* Brand */}
          <Link to="/" className="group mb-8 flex items-center gap-3 px-1 pt-1">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-blue-600 via-blue-500 to-cyan-400 text-white shadow-[0_8px_22px_-8px_rgba(37,99,235,0.8)] ring-1 ring-white/20 transition group-hover:brightness-110">
              <PlayIcon width={18} height={18} />
            </span>
            <div className="leading-tight">
              <div className="brand-text font-display text-xl font-bold">CoreClip</div>
            </div>
          </Link>

          {/* Nav */}
          <nav className="flex flex-col gap-1">
            {topNav.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
                    isActive
                      ? 'text-white'
                      : 'text-[var(--text-dim)] hover:bg-white/[0.04] hover:text-white'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && (
                      <span className="absolute inset-0 rounded-xl bg-gradient-to-r from-blue-500/22 to-cyan-500/14 ring-1 ring-inset ring-blue-400/30" />
                    )}
                    {isActive && (
                      <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-full bg-gradient-to-b from-blue-400 to-cyan-400 shadow-[0_0_12px_2px_rgba(37,99,235,0.7)]" />
                    )}
                    <Icon
                      width={18}
                      height={18}
                      className={`relative ${isActive ? 'text-cyan-300' : ''}`}
                    />
                    <span className="relative">{label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          {/* Primary action */}
          <Link to="/new" className="btn btn-primary mt-6 w-full">
            <PlusIcon width={16} height={16} />
            New Clipping Job
          </Link>

          {/* Bottom nav — pushed to the bottom of the sidebar */}
          <nav className="mt-auto flex flex-col gap-1 border-t border-white/10 pt-4">
            {bottomNav.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
                    isActive
                      ? 'text-white'
                      : 'text-[var(--text-dim)] hover:bg-white/[0.04] hover:text-white'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && (
                      <span className="absolute inset-0 rounded-xl bg-gradient-to-r from-blue-500/22 to-cyan-500/14 ring-1 ring-inset ring-blue-400/30" />
                    )}
                    {isActive && (
                      <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-full bg-gradient-to-b from-blue-400 to-cyan-400 shadow-[0_0_12px_2px_rgba(37,99,235,0.7)]" />
                    )}
                    <Icon
                      width={18}
                      height={18}
                      className={`relative ${isActive ? 'text-cyan-300' : ''}`}
                    />
                    <span className="relative">{label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          <div className="px-1 pt-4">
            <div className="text-[10px] text-[var(--text-faint)]/70">
              v1.0
            </div>
          </div>
        </div>
      </aside>

      {/* ── Main scroll region ───────────────────────────────────── */}
      <main className="relative z-10 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1320px] px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
