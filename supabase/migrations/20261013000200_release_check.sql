-- Endpoint kiem update, va ly do no phai ton tai o day.
--
-- ⛔ KHONG CO CAI NAY THI APP DUNG O MAN HINH LOI. `SecureBootstrap.tsx:62`
-- goi `await invoke('check_backend_update')` KHONG co try/catch, ngay truoc
-- `launch_backend`. Launcher goi `{base}/v1/releases/check`, nen neu chi deploy
-- hai function activate va lease thi duong do tra 404, lenh invoke nem loi,
-- `.catch()` o useEffect dat phase = 'error', VA BACKEND KHONG BAO GIO CHAY.
-- Kich hoat thanh cong van khong dung duoc app. Do la ly do file nay ton tai,
-- khong phai de cho du tinh nang.
--
-- ⛔ VA KHONG CO BANG `releases` NAO O DAY, CO Y. Phat hanh ban moi can hai
-- thu nua: manifest DA KY bang khoa release, va ve tai mot lan cho artifact.
-- Khoa release KHONG duoc dua len Supabase - chiem duoc khoa lease chi la dung
-- lau app mien phi, con chiem duoc khoa release la GUI DUOC BAN CAI DOC cho
-- khach. Nen endpoint nay luon tra `update_available: false`, va do la cau tra
-- loi DUNG: khong co ban phat hanh nao duoc ghi nhan o day.
--
-- Hai hau qua, noi ro de khong ai tuong la da co:
--   1. KHONG CO AUTO-UPDATE tren duong Supabase. Gui ban backend moi bang tay.
--   2. KHONG CO UPDATE BAT BUOC. Co che `mandatory` khong co duong nao de bat.
-- Muon co ca hai thi phai dung control plane Python (co `/v1/releases/check`
-- va `/v1/artifacts/{id}/download` day du) sau mot tunnel - xem
-- docs/SUPABASE-KEY.md.

create or replace function public.tkauto_release_check(
    p_device_id    text,
    p_license_id   text,
    p_nonce_digest text,
    p_nonce_ttl    integer,
    p_rate_bucket  text,
    p_rate_limit   integer,
    p_rate_window  integer
) returns jsonb
language plpgsql
security definer
set search_path = license, pg_catalog
as $$
declare
    v_now     bigint := extract(epoch from now())::bigint;
    v_device  devices;
    v_license licenses;
begin
    if not license.claim_rate_slot(p_rate_bucket, p_rate_limit, p_rate_window) then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    if not license.claim_nonce(p_nonce_digest, p_nonce_ttl) then
        return jsonb_build_object('ok', false, 'code', 'nonce_replayed');
    end if;

    select * into v_device from license.devices where id = p_device_id;
    if not found
       or v_device.license_id <> p_license_id
       or v_device.revoked_at is not null then
        return jsonb_build_object('ok', false, 'code', 'device_invalid');
    end if;

    select * into v_license from license.licenses where id = p_license_id;
    if not found
       or v_license.revoked_at is not null
       or v_license.expires_at <= v_now then
        return jsonb_build_object('ok', false, 'code', 'license_revoked_or_expired');
    end if;

    -- Khong ghi audit cho moi lan kiem update: launcher goi moi lan mo app,
    -- va mot bang audit day nhung dong "da kiem, khong co gi" se chon nhung
    -- dong dang doc.
    return jsonb_build_object('ok', true);
end;
$$;

revoke all on function public.tkauto_release_check(text, text, text, integer, text, integer, integer) from public;

do $$
declare
    role_name text;
    signature text := 'public.tkauto_release_check(text, text, text, integer, text, integer, integer)';
begin
    foreach role_name in array array['anon', 'authenticated']
    loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format('revoke all on function %s from %I', signature, role_name);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        execute format('grant execute on function %s to service_role', signature);
    end if;
end
$$;
