// Chay THAT handler cua mot Edge Function tren Node, khong can Docker.
//
// ⛔ VI SAO CAN CAI NAY. Cach dung de thu hai function la `supabase start`, va
// no can Docker - tren may nay Docker Desktop co cai nhung service bi Stopped
// va bat no can quyen admin. Khong co cho nay thi lan dau tien hai handler
// chay that la tren du an Supabase cua khach, voi key that trong DB.
//
// Harness lam ba viec, va chi ba viec:
//   1. dung `globalThis.Deno` gia DU de module nap duoc va GIU LAI handler
//      (module goi `Deno.serve(...)` ngay khi import, nen Deno phai co TRUOC);
//   2. thay `fetch` de cac lenh goi RPC tra ve phan xet duoc dat san;
//   3. goi handler voi mot Request that roi tra ve status + body + nhung lan
//      goi RPC da xay ra.
//
// ⛔ NHUNG NO KHONG THAY DUOC POSTGRES. Phan xet RPC o day la do bai test dat,
// nen harness KHONG kiem duoc logic trong plpgsql: khoa hang, dem thiet bi,
// chong phat lai nonce. Nhung thu do phai duoc kiem bang mot kich hoat that.
// Cai duoc kiem o day la toan bo phan TypeScript: kiem dau vao, suy ra
// device_id, xac thuc chu ky, dung digest, va ky lease.

interface Job {
  fn: "activate" | "lease" | "releases" | "artifacts";
  env: Record<string, string>;
  request: {
    url: string;
    method?: string;
    headers?: Record<string, string>;
    body?: unknown;
  };
  /** Phan xet tra ve cho tung ten RPC, theo thu tu goi. */
  rpc: Record<string, unknown[]>;
  /**
   * Phan hoi cho lenh ky signed URL cua Storage.
   * Bo trong = chua dat (harness tra 500); `null` = gia lap Storage su co.
   */
  storage?: { signedURL: string } | null;
}

function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk as Buffer));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

const job: Job = JSON.parse(await readStdin());

let handler: ((request: Request) => Promise<Response>) | null = null;
const rpcCalls: { name: string; args: unknown }[] = [];
const storageCalls: { path: string; body: unknown }[] = [];
const pending: Record<string, unknown[]> = {};
for (const [name, list] of Object.entries(job.rpc ?? {})) {
  pending[name] = [...list];
}

(globalThis as Record<string, unknown>).Deno = {
  serve: (fn: (request: Request) => Promise<Response>) => {
    handler = fn;
  },
  env: {
    get: (key: string): string | undefined => job.env[key],
  },
};

(globalThis as Record<string, unknown>).fetch = async (
  input: string | URL | Request,
  init?: RequestInit,
): Promise<Response> => {
  const url = typeof input === "string" ? input : input.toString();

  // ⛔ CHAN LOI GOI STORAGE API. Function `artifacts` ky signed URL qua
  // `/storage/v1/object/sign/...`; khong chan thi bai test di ra Internet
  // that va phu thuoc mot bucket that.
  const storage = /\/storage\/v1\/object\/sign\/(.+)$/.exec(url);
  if (storage) {
    storageCalls.push({ path: storage[1], body: init?.body ? JSON.parse(String(init.body)) : null });
    if (job.storage === undefined) {
      return new Response(JSON.stringify({ message: "harness khong co phan xet storage" }), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    }
    if (job.storage === null) {
      return new Response("storage loi", { status: 503 });
    }
    return new Response(JSON.stringify(job.storage), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }

  const match = /\/rest\/v1\/rpc\/([A-Za-z0-9_]+)$/.exec(url);
  if (!match) throw new Error(`harness khong biet URL nay: ${url}`);
  const name = match[1];
  const args = init?.body ? JSON.parse(String(init.body)) : null;
  rpcCalls.push({ name, args });
  const queue = pending[name];
  if (!queue || queue.length === 0) {
    // ⛔ KHONG tra ve mot phan xet mac dinh. Mot lan goi RPC khong duoc du
    // tinh la mot thay doi hanh vi, va bai test phai thay no.
    return new Response(JSON.stringify({ detail: `harness khong co phan xet cho ${name}` }), {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  }
  return new Response(JSON.stringify(queue.shift()), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};

const modules = {
  activate: "../activate/index.ts",
  lease: "../lease/index.ts",
  releases: "../releases/index.ts",
  artifacts: "../artifacts/index.ts",
};
await import(new URL(modules[job.fn], import.meta.url).href);

if (handler === null) {
  throw new Error(`module ${job.fn} khong goi Deno.serve`);
}

const request = new Request(job.request.url, {
  method: job.request.method ?? "POST",
  headers: { "content-type": "application/json", ...(job.request.headers ?? {}) },
  body: job.request.body === undefined ? undefined : JSON.stringify(job.request.body),
});

// ⛔ KHONG de fetch cua handler di ra Internet that. Function `artifacts` goi
// Storage API de ky signed URL; neu khong chan thi bai test lai phu thuoc
// mang va vao mot bucket that.
const response = await (handler as (request: Request) => Promise<Response>)(request);
const text = await response.text();
let parsed: unknown = text;
try {
  parsed = JSON.parse(text);
} catch {
  // de nguyen dang chuoi
}

process.stdout.write(
  JSON.stringify({
    status: response.status,
    headers: Object.fromEntries(response.headers.entries()),
    body: parsed,
    rpc_calls: rpcCalls,
    storage_calls: storageCalls,
  }),
);
