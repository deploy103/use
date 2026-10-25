import { test, expect } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { mockBackend, oauthFixture } from "./fixtures";

async function capture(page: Page, info: TestInfo, state: string) {
  const dir = path.resolve("../.amp/in/artifacts/use-frontend");
  await mkdir(dir, { recursive: true });
  await page.screenshot({
    path: path.join(dir, `synthetic-${info.project.name}-${state}.png`),
    fullPage: true,
  });
}
async function navigate(page: Page, name: string) {
  if (await page.getByRole("button", { name: "메뉴 열기" }).isVisible())
    await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name, exact: true }).click();
}
async function openCustom(page: Page) {
  await page.getByText("기타 +", { exact: true }).click();
  await page.getByRole("button", { name: "커스텀 API 추가" }).click();
}

test("quota-first dashboard, themes, account controls and screenshots", async ({
  page,
}, info) => {
  const state = await mockBackend(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard");
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute(
    "href",
    /^data:image\/svg\+xml,/,
  );
  await expect(
    page.getByRole("heading", { name: "Account quotas" }),
  ).toBeVisible();
  await expect(
    page.getByRole("progressbar", { name: "5시간 한도 사용률" }),
  ).toHaveAttribute("aria-valuenow", "17.5");
  await expect(page.getByText("잔여 82.5 % / 100")).toBeVisible();
  await expect(
    page.getByText("구독 한도와 날짜별 사용 기록은 다릅니다"),
  ).toBeVisible();
  await expect(page.locator("canvas")).toHaveCount(0);
  await expect(page.getByText("기간 토큰", { exact: true })).toHaveCount(0);
  const firstQuota = await page
    .getByRole("progressbar", { name: "5시간 한도 사용률" })
    .boundingBox();
  expect(firstQuota).not.toBeNull();
  expect(firstQuota!.y).toBeLessThan(page.viewportSize()!.height);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await capture(page, info, "dashboard-dark");
  await page.getByRole("button", { name: "라이트 테마" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await capture(page, info, "dashboard-light");
  await page.getByRole("button", { name: "다크 테마" }).click();
  await navigate(page, "Accounts");
  await expect(page).toHaveURL(/\/accounts$/);
  await capture(page, info, "accounts");
  await page.getByLabel("계정 상태 필터").selectOption("disabled");
  await expect(page.getByRole("article")).toHaveCount(1);
  await expect(page.getByRole("article")).toContainText("비활성");
  await page.getByLabel("계정 상태 필터").selectOption("all");
  await page.getByRole("button", { name: "Personal ChatGPT 편집" }).click();
  await expect(page.getByLabel("API 기본 URL")).toHaveCount(0);
  await page.getByLabel("계정 이름", { exact: true }).fill("Renamed ChatGPT");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Renamed ChatGPT", exact: true }),
  ).toBeVisible();
  const edit = state.calls.find((call) => call.method === "PATCH");
  expect(edit?.body).toEqual({ name: "Renamed ChatGPT", enabled: true });
  await page.getByLabel("Renamed ChatGPT 선택").check();
  await page.getByRole("button", { name: "비활성화", exact: true }).click();
  await expect(
    page.getByRole("article", { name: "Renamed ChatGPT 계정" }),
  ).toContainText("비활성");
  await page.getByRole("button", { name: "Renamed ChatGPT 삭제" }).click();
  await page.getByRole("button", { name: "취소", exact: true }).click();
  expect(
    state.calls.filter(
      (c) => c.method === "DELETE" && c.path.startsWith("/api/accounts"),
    ),
  ).toHaveLength(0);
  await page.getByRole("button", { name: "Renamed ChatGPT 삭제" }).click();
  await page.getByRole("button", { name: "삭제 확인" }).click();
  await expect(
    page.getByRole("article", { name: "Renamed ChatGPT 계정" }),
  ).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("custom API defaults, explicit test, mutation, unsupported is not zero", async ({
  page,
}, info) => {
  const state = await mockBackend(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Account quotas" }),
  ).toBeVisible();
  await openCustom(page);
  await page
    .getByLabel("API 기본 URL")
    .fill("https://usage.example.com:8443/team/v1");
  await page.getByLabel("조회용 API 키").fill("synthetic-key-not-real");
  await capture(page, info, "custom-config");
  await page.getByRole("button", { name: "연결 테스트", exact: true }).click();
  await expect(page.getByText(/사용량 조회 확인/)).toBeVisible();
  expect(state.calls.filter((c) => c.path === "/api/accounts")).toHaveLength(0);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const saved = state.calls.find((c) => c.path === "/api/accounts");
  expect(saved?.body).toMatchObject({
    provider: "custom-api",
    base_url: "https://usage.example.com:8443/team/v1",
    credential: "synthetic-key-not-real",
    extra: { usage_path: "/v1/usage", format: "auto", auth_type: "auto" },
  });
  expect(
    state.calls.filter((c) => c.path === "/api/connections/test"),
  ).toHaveLength(1);
  await openCustom(page);
  await page.getByLabel("API 기본 URL").fill("https://public.example.com");
  await expect(page.getByLabel("조회용 API 키")).not.toHaveAttribute(
    "required",
  );
  await page
    .getByRole("button", { name: "테스트 후 저장", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    state.calls.filter((c) => c.path === "/api/accounts").at(-1)?.body,
  ).toMatchObject({
    provider: "custom-api",
    base_url: "https://public.example.com",
    credential: "",
  });
  expect(
    state.calls.filter((c) => c.path === "/api/connections/test").at(-1)?.body,
  ).toMatchObject({ credential: "" });
  state.testReadable = false;
  await openCustom(page);
  await page.getByLabel("API 기본 URL").fill("https://inference.example.com");
  await page.getByLabel("조회용 API 키").fill("synthetic-only");
  await page
    .getByRole("button", { name: "테스트 후 저장", exact: true })
    .click();
  await expect(
    page.getByText(/사용량 API 미지원 또는 읽기 실패/),
  ).toBeVisible();
  expect(state.calls.filter((c) => c.path === "/api/accounts")).toHaveLength(2);
  const storage = await page.evaluate(() => ({ ...localStorage }));
  expect(storage).toEqual({ "use-theme": "dark" });
  expect(state.unexpected).toEqual([]);
});

test("Claude first-class key modes, fixed Admin origin, no consumer OAuth", async ({
  page,
}, info) => {
  const state = await mockBackend(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Claude 연결", exact: true }).click();
  await expect(page.getByLabel("Claude 연결 범위")).toHaveValue("claude-lb");
  await expect(
    page.getByText(/Claude.ai Pro\/Max 소비자 계정 로그인은 제공하지 않습니다/),
  ).toBeVisible();
  await capture(page, info, "claude-lb");
  await page.getByLabel("Claude 연결 범위").selectOption("claude");
  await expect(page.getByLabel("API 기본 URL")).toHaveValue(
    "https://api.anthropic.com",
  );
  await expect(page.getByLabel("API 기본 URL")).toHaveAttribute("readonly", "");
  await expect(page.getByLabel("Anthropic Admin API 키")).toHaveAttribute(
    "required",
    "",
  );
  await page.getByLabel("Anthropic Admin API 키").fill("synthetic-admin-key");
  await page
    .getByRole("button", { name: "테스트 후 저장", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    state.calls.find((c) => c.path === "/api/accounts")?.body,
  ).toMatchObject({
    provider: "claude",
    base_url: "https://api.anthropic.com",
  });
  expect(state.calls.some((c) => c.path.startsWith("/api/oauth"))).toBe(false);
  expect(state.unexpected).toEqual([]);
});

test("Gemini code flow and ChatGPT callback URL flow", async ({
  page,
}, info) => {
  const state = await mockBackend(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Gemini 연결", exact: true }).click();
  await page.getByRole("button", { name: "연결 시작", exact: true }).click();
  const link = page.getByRole("link", { name: "공식 인증 페이지 열기" });
  await expect(link).toHaveAttribute("href", /https:\/\/accounts.google.com\//);
  await capture(page, info, "provider-login");
  await page.getByLabel("인증 코드", { exact: true }).fill("bad#state");
  await page.getByRole("button", { name: "인증 완료", exact: true }).click();
  await expect(
    page.getByText(/Google 인증 코드를 단독으로 붙여넣으세요/).first(),
  ).toBeVisible();
  expect(state.calls.filter((c) => c.path.endsWith("/complete"))).toHaveLength(
    0,
  );
  await page
    .getByLabel("인증 코드", { exact: true })
    .fill("synthetic-google-code");
  await page.getByRole("button", { name: "인증 완료", exact: true }).click();
  await expect(page.getByText("계정이 연결되었습니다.")).toBeVisible();
  expect(state.calls.find((c) => c.path.endsWith("/complete"))?.body).toEqual({
    callback_input: "synthetic-google-code",
  });
  await page.getByRole("button", { name: "완료", exact: true }).click();
  state.flow = oauthFixture("chatgpt");
  await page.getByRole("button", { name: "ChatGPT 연결", exact: true }).click();
  await page.getByRole("button", { name: "연결 시작", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "공식 인증 페이지 열기" }),
  ).toHaveAttribute("href", /https:\/\/auth.openai.com\//);
  await page
    .getByLabel("인증 후 돌아온 전체 주소")
    .fill("http://localhost:1455/auth/callback?code=synthetic&state=synthetic");
  await page.getByRole("button", { name: "인증 완료", exact: true }).click();
  await expect(page.getByText("계정이 연결되었습니다.")).toBeVisible();
  expect(
    state.calls.filter((c) => c.path.endsWith("/complete")).at(-1)?.body,
  ).toEqual({
    callback_input:
      "http://localhost:1455/auth/callback?code=synthetic&state=synthetic",
  });
  expect(state.unexpected).toEqual([]);
});

test("OAuth untrusted origins rejected and late starts cancelled after close", async ({
  page,
}) => {
  const state = await mockBackend(page);
  state.flow = {
    ...oauthFixture("gemini"),
    authorization_url: "https://accounts.google.com.attacker.example/auth",
  };
  await page.goto("/");
  await page.getByRole("button", { name: "Gemini 연결", exact: true }).click();
  await page.getByRole("button", { name: "연결 시작", exact: true }).click();
  await expect(
    page.getByText(/허용된 공식 HTTPS 인증 주소가 아니므로/),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "공식 인증 페이지 열기" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "취소 · 닫기" }).click();
  const prior = state.calls.filter((c) => c.method === "DELETE").length;
  state.startDelay = 500;
  state.flow = oauthFixture("gemini");
  await page.getByRole("button", { name: "Gemini 연결", exact: true }).click();
  await page.getByRole("button", { name: "연결 시작", exact: true }).click();
  await page.getByRole("button", { name: "취소 · 닫기" }).click();
  await expect
    .poll(() => state.calls.filter((c) => c.method === "DELETE").length)
    .toBeGreaterThan(prior);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("expiry cannot be resurrected by late complete response", async ({
  page,
}) => {
  const state = await mockBackend(page);
  state.flow = { ...oauthFixture("gemini"), expires_at: Date.now() + 3500 };
  state.completeDelay = 4500;
  await page.goto("/");
  await page.getByRole("button", { name: "Gemini 연결", exact: true }).click();
  await page.getByRole("button", { name: "연결 시작", exact: true }).click();
  await page.getByLabel("인증 코드", { exact: true }).fill("synthetic-code");
  await page.getByRole("button", { name: "인증 완료", exact: true }).click();
  await expect(page.getByText(/인증 세션이 만료되었습니다/)).toBeVisible({
    timeout: 6000,
  });
  await page.waitForTimeout(2500);
  await expect(page.getByText("계정이 연결되었습니다.")).toHaveCount(0);
  expect(state.calls.some((c) => c.method === "DELETE")).toBe(true);
});

test("dated charts render actual data and retain previous on server error", async ({
  page,
}, info) => {
  const state = await mockBackend(page, true);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/usage");
  await expect(
    page.getByRole("heading", { name: "Usage over time" }),
  ).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
  expect(await page.locator("canvas").count()).toBeGreaterThan(1);
  await expect
    .poll(() =>
      page.locator("canvas").evaluateAll((canvases) =>
        Math.max(
          ...canvases.map((canvas) => {
            if (!(canvas instanceof HTMLCanvasElement)) return 0;
            const context = canvas.getContext("2d");
            if (!context || !canvas.width || !canvas.height) return 0;
            const pixels = context.getImageData(
              0,
              0,
              canvas.width,
              canvas.height,
            ).data;
            let painted = 0;
            for (let i = 3; i < pixels.length; i += 4)
              if (pixels[i] > 0) painted++;
            return painted;
          }),
        ),
      ),
    )
    .toBeGreaterThan(100);
  await expect(
    page.getByText("synthetic-model", { exact: true }),
  ).toBeVisible();
  await capture(page, info, "dated-usage");
  await page.getByText("날짜별 수치 보기 · 미제공 값은 —").click();
  await expect(
    page.getByRole("cell", { name: "2026-10-23", exact: true }),
  ).toBeVisible();
  await page.getByLabel("차트 지표").selectOption("cost_usd");
  await expect(page.getByRole("img", { name: /보고된/ })).toHaveAttribute(
    "aria-label",
    /보고된 5일/,
  );
  state.failDashboard = true;
  await page.getByLabel("조회 기간").selectOption("30d");
  await expect(page.getByRole("alert")).toContainText(
    "마지막으로 불러온 데이터를 유지합니다",
  );
  await expect(
    page.getByText("synthetic-model", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "CSV" })).toHaveAttribute(
    "href",
    "/api/export?range=30d&provider=all&account=all",
  );
  state.failDashboard = false;
  await page.getByRole("button", { name: "다시 시도", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("expired owner session clears provider dialog and does not persist secrets", async ({
  page,
}) => {
  const state = await mockBackend(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Gemini 연결", exact: true }).click();
  await page.getByRole("button", { name: "연결 시작", exact: true }).click();
  await page
    .getByLabel("인증 코드", { exact: true })
    .fill("synthetic-sensitive-code");
  state.authenticated = false;
  await expect(
    page.getByRole("heading", { name: "관리자 로그인" }),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByLabel("첫 설정 토큰")).toHaveCount(0);
  await expect(page.getByText(/관리자 세션이 만료되었습니다/)).toBeVisible();
  await expect
    .poll(() =>
      state.calls.some(
        (c) => c.method === "DELETE" && c.path.startsWith("/api/oauth/"),
      ),
    )
    .toBe(true);
  expect(await page.evaluate(() => ({ ...localStorage }))).toEqual({
    "use-theme": "dark",
  });
  expect(state.unexpected).toEqual([]);
});

test("owner setup hash removed, configured login hides token, settings password contract", async ({
  page,
}, info) => {
  const state = await mockBackend(page);
  state.authenticated = false;
  state.setup = true;
  await page.goto("/#setup=synthetic-first-use-token");
  await expect(page.getByLabel("첫 설정 토큰")).toHaveValue(
    "synthetic-first-use-token",
  );
  expect(new URL(page.url()).hash).toBe("");
  await page.getByLabel("새 관리자 비밀번호").fill("synthetic-owner-password");
  await page.getByRole("button", { name: "워크스페이스 만들기" }).click();
  await expect(
    page.getByRole("heading", { name: "Account quotas" }),
  ).toBeVisible();
  expect(state.calls.find((c) => c.path === "/api/setup")?.body).toEqual({
    token: "synthetic-first-use-token",
    password: "synthetic-owner-password",
  });
  await navigate(page, "Settings");
  await capture(page, info, "settings");
  await page
    .getByLabel("현재 관리자 비밀번호")
    .fill("synthetic-owner-password");
  await page
    .getByLabel("새 관리자 비밀번호", { exact: true })
    .fill("synthetic-replacement-password");
  await page
    .getByLabel("새 비밀번호 확인")
    .fill("synthetic-replacement-password");
  await page.getByRole("button", { name: "관리자 비밀번호 변경" }).click();
  await expect(page.getByText(/비밀번호를 변경했습니다/)).toBeVisible();
  expect(
    state.calls.find((c) => c.path === "/api/settings/password")?.body,
  ).toEqual({
    current_password: "synthetic-owner-password",
    new_password: "synthetic-replacement-password",
  });
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "관리자 로그인" }),
  ).toBeVisible();
  await expect(page.getByLabel("첫 설정 토큰")).toHaveCount(0);
  const storage = await page.evaluate(() => ({ ...localStorage }));
  expect(storage).toEqual({ "use-theme": "dark" });
  expect(state.unexpected).toEqual([]);
});
