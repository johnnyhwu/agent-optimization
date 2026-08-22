import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Field, { FormSection } from "../ui/Field.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { useToast } from "../Toast.jsx";
import { IconAlert, IconCheck } from "../icons.jsx";
import {
  changedKeys,
  errorsOf,
  fromStored,
  hasErrors,
  overrides,
} from "../../settings_fields.js";
import SettingRow from "./SettingRow.jsx";

// Where a developer stops retyping the same values.
//
// Every form in the three sections opens on what the deployment configured. That
// is right for a deployment and wrong for a person: somebody who points every
// run at their own agent server types that address a dozen times a day, and the
// "Run eval" dialog has no memory of yesterday. This is where they say it once.
//
// The page is generated from the catalogue the backend serves, rather than
// written out field by field. Not for brevity — for the property that matters
// six months from now: a setting added to `settings_catalog.py` appears here
// with no edit to this file, so the page cannot fall behind the product. The
// two contract tests (`backend/tests/test_settings_catalog.py` and
// `src/settings_catalog.test.js`) are what make sure the catalogue itself does
// not fall behind either.
export default function DefaultsPanel() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .userSettings()
      .then((r) => {
        setData(r);
        setForm(fromStored(r.catalog, r.values));
      })
      .catch((e) => setError(e.message));
  }, []);

  const errors = useMemo(
    () => (data && form ? errorsOf(data.catalog, form) : {}),
    [data, form]
  );
  const changed = useMemo(
    () => (data && form ? changedKeys(data.catalog, form) : []),
    [data, form]
  );

  if (error) return <div className="error">{error}</div>;
  if (!data || !form) return <Skeleton variant="row" count={6} />;

  const unseen = new Set(data.unseen);

  async function save() {
    setSaving(true);
    try {
      const values = overrides(data.catalog, form);
      await api.saveUserSettings(values);
      // Re-read rather than trusting the form: the server is what decides what
      // was stored, and `drifted` is recomputed from it.
      const fresh = await api.userSettings();
      setData(fresh);
      setForm(fromStored(fresh.catalog, fresh.values));
      toast.show("Defaults saved");
    } catch (e) {
      toast.show(e.message, "error");
    } finally {
      setSaving(false);
    }
  }

  async function acknowledge() {
    try {
      await api.markSettingsSeen(data.unseen);
      setData({ ...data, unseen: [] });
    } catch (e) {
      toast.show(e.message, "error");
    }
  }

  return (
    <div className="settings-panel">
      <div className="settings-head">
        <div>
          <h2>Your defaults</h2>
          <p className="hint">
            What every form in Evaluation, Playground and Optimize opens with. An
            empty field follows this deployment — its value is the grey text.
            Type to override it, clear it to go back.
          </p>
        </div>
        <div className="settings-actions">
          <span className="hint">
            {changed.length === 0
              ? "Nothing overridden"
              : `${changed.length} overridden`}
          </span>
          <Button
            variant="primary"
            onClick={save}
            disabled={saving || hasErrors(data.catalog, form)}
            icon={<IconCheck size={14} />}
          >
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>

      {/* Two things that need looking at, and neither is an error. The first is
          the only reason the row remembers which keys have been shown; the
          second is the one that actually breaks people — an admin repoints
          LLM_BASE_URL, and everyone who overrode it keeps talking to the host
          that went away with nothing on screen to explain why it is only them. */}
      {data.unseen.length > 0 && (
        <Banner
          tone="info"
          title={`${data.unseen.length} new setting${data.unseen.length === 1 ? "" : "s"} since you last looked`}
        >
          {data.unseen.map((k) => labelOf(data.catalog, k)).join(", ")}. They are
          using this deployment's values until you say otherwise.{" "}
          <Button size="sm" onClick={acknowledge}>Got it</Button>
        </Banner>
      )}

      {data.drifted.length > 0 && (
        <Banner tone="warning" title="This deployment changed a value you had overridden">
          {data.drifted.map((d) => (
            <div key={d.key}>
              <strong>{labelOf(data.catalog, d.key)}</strong>: was{" "}
              <code>{String(d.was)}</code>, now <code>{String(d.now)}</code>. Yours
              is still in use.
            </div>
          ))}
        </Banner>
      )}

      {data.invalid.length > 0 && (
        <Banner tone="error" title="Some saved values are no longer valid">
          {data.invalid.join(", ")} — these are being ignored and this deployment's
          values used instead. Set them again to fix it.
        </Banner>
      )}

      {data.groups.map((group) => {
        const specs = data.catalog.filter((s) => s.group === group.id);
        if (specs.length === 0) return null;
        return (
          <FormSection key={group.id} title={group.label} description={group.description}>
            {specs.map((spec) =>
              spec.kind === "secret" ? (
                <SecretRow
                  key={spec.key}
                  spec={spec}
                  state={data.secrets[spec.key]}
                  available={data.secrets_available}
                  reason={data.secrets_unavailable_reason}
                  endpointValue={
                    form[spec.endpoint_key]?.raw?.trim() ||
                    data.system[spec.endpoint_key] ||
                    ""
                  }
                  onChanged={async () => setData(await api.userSettings())}
                />
              ) : (
                <SettingRow
                  key={spec.key}
                  spec={spec}
                  entry={form[spec.key]}
                  system={data.system[spec.key]}
                  error={errors[spec.key]}
                  isNew={unseen.has(spec.key)}
                  onChange={(next) => setForm({ ...form, [spec.key]: next })}
                />
              )
            )}
          </FormSection>
        );
      })}
    </div>
  );
}

function labelOf(catalog, key) {
  return catalog.find((s) => s.key === key)?.label || key;
}

// A credential, which behaves differently from everything else on this page and
// says so.
//
// It is never read back — the field is always blank and the line underneath is
// the only report of what is stored. It saves on its own button rather than with
// the rest of the form, because it goes to its own endpoint: a secret must not
// share a request body with values that are safe to log.
//
// And it is bound to the endpoint it was stored against. Change the base URL and
// this has to be entered again — deliberate friction, because the alternative is
// sending the developer's key to whatever address they typed next.
function SecretRow({ spec, state, available, reason, endpointValue, onChanged }) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  if (!available) {
    return (
      <Field label={spec.label}>
        <div className="hint">
          <IconAlert size={13} /> {reason}
        </div>
      </Field>
    );
  }

  const bound = state?.set ? state.endpoint : "";
  const stale = state?.set && bound !== endpointValue;

  async function save() {
    setBusy(true);
    try {
      await api.saveUserSecret(spec.key, value, endpointValue);
      setValue("");
      await onChanged();
      toast.show(`${spec.label} saved`);
    } catch (e) {
      toast.show(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    setBusy(true);
    try {
      await api.deleteUserSecret(spec.key);
      await onChanged();
      toast.show(`${spec.label} removed`);
    } catch (e) {
      toast.show(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Field
      label={spec.label}
      help={spec.help}
      error={
        stale
          ? `Stored for ${bound || "(blank)"}, which is not the address above. It will not be used until you enter it again.`
          : undefined
      }
    >
      <div className="secret-row">
        <input
          type="password"
          autoComplete="new-password"
          value={value}
          placeholder={state?.set ? "Stored — type to replace" : ""}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button size="sm" onClick={save} disabled={busy || !value}>
          Save
        </Button>
        {state?.set && (
          <Button size="sm" variant="danger" onClick={forget} disabled={busy}>
            Remove
          </Button>
        )}
      </div>
      <div className="hint">
        {state?.set ? (
          state.readable ? (
            <>Stored for <code>{bound || "(blank)"}</code>. It is never sent back to this page.</>
          ) : (
            <>
              <IconAlert size={13} /> Stored, but this deployment can no longer
              read it — enter it again.
            </>
          )
        ) : (
          <>Not stored. Runs will use whatever is typed into their own form.</>
        )}
      </div>
    </Field>
  );
}
