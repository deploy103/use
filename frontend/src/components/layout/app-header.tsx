// Adapted from Soju06/codex-lb app-header (MIT): sticky header, action controls,
// active nav treatment and mobile navigation. Gateway permissions are not used.
import {
  Activity,
  LayoutDashboard,
  Users,
  Settings,
  Menu,
  Moon,
  Sun,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export type Page = "dashboard" | "accounts" | "usage" | "settings";
const nav = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "accounts", label: "Accounts", icon: Users },
  { id: "usage", label: "Usage", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings },
] as const;

export function AppHeader({
  page,
  navigate,
  dark,
  toggleTheme,
  logout,
  children,
}: {
  page: Page;
  navigate: (page: Page) => void;
  dark: boolean;
  toggleTheme: () => void;
  logout: () => void;
  children: React.ReactNode;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const links = (
    <nav aria-label="주 메뉴" className="space-y-1">
      {nav.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          aria-current={page === id ? "page" : undefined}
          onClick={() => {
            navigate(id);
            setMobileOpen(false);
          }}
          className={cn(
            "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
            page === id
              ? "bg-primary/10 text-primary"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Icon size={17} aria-hidden="true" />
          {label}
        </button>
      ))}{" "}
    </nav>
  );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        본문으로 건너뛰기
      </a>
      <aside className="rail">
        <a
          href="#dashboard"
          onClick={(e) => {
            e.preventDefault();
            navigate("dashboard");
          }}
          className="brand"
        >
          <span className="brand-mark">AI</span>
          <span>
            AI 대시보드<span className="brand-caption">AI USAGE WORKSPACE</span>
          </span>
        </a>
        <p className="rail-label">WORKSPACE</p>
        {links}
        <div className="rail-bottom">
          <ShieldCheck size={16} />
          <span>
            Owner workspace<small>조회 전용 · 비공개</small>
          </span>
        </div>
      </aside>
      <div className="workspace">
        <header className="sticky top-0 z-20 border-b bg-background/80 px-4 py-3 backdrop-blur-xl md:px-8">
          <div className="mx-auto flex w-full max-w-[1500px] items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                size="icon-sm"
                className="md:hidden"
                aria-label="메뉴 열기"
                onClick={() => setMobileOpen(true)}
              >
                <Menu />
              </Button>
              <span className="text-xs text-muted-foreground">
                AI 대시보드 <span className="mx-2 opacity-40">/</span>
                <span className="text-foreground">
                  {nav.find((n) => n.id === page)?.label}
                </span>
              </span>
            </div>
            <div className="flex items-center gap-1">
              <span className="mr-3 hidden text-xs text-muted-foreground sm:block">
                <span className="status-dot" /> Owner session
              </span>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={dark ? "라이트 테마" : "다크 테마"}
                onClick={toggleTheme}
              >
                {dark ? <Sun /> : <Moon />}
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="로그아웃"
                onClick={logout}
              >
                <LogOut />
              </Button>
            </div>
          </div>
        </header>
        <main
          id="main"
          tabIndex={-1}
          className="mx-auto w-full max-w-[1564px] space-y-6 p-4 md:p-8"
        >
          {children}
        </main>
        <footer className="workspace-footer">
          AI 대시보드{" "}
          <span>Read-only upstream · 구독 한도와 사용 기록을 구분합니다</span>
        </footer>
      </div>
      <Dialog open={mobileOpen} onOpenChange={setMobileOpen}>
        <DialogContent>
          <DialogTitle>AI 대시보드</DialogTitle>
          <DialogDescription>페이지 이동</DialogDescription>
          {links}
        </DialogContent>
      </Dialog>
    </div>
  );
}
