import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api.js";
import Banner, { BannerDetail } from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Field, { FormSection } from "../ui/Field.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { useToast } from "../Toast.jsx";
import { IconAlert, IconCheck, IconSearch, IconX } from "../icons.jsx";
import {
  changedKeys,
  errorsOf,
  fromStored,
  hasErrors,
  overrides,
} from "../../settings_fields.js";
import { barMessage, dirtyKeys, visibleGroups } from "../../settings_view.js";
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
//
// That generosity is also the shape of the problem this page had. Twenty-five
// rows over six groups is longer than a window, and the page was written as if
// it were a dialog: Save sat in the heading, so from the last group — the six
// early-stopping fields, the ones somebody actually tunes repeatedly — the only
// way to commit was to scroll back past everything. Worse, nothing on screen
// distinguished *typed* from *saved*, so the page you had edited and the page
// you had not looked like the same page, and closing the tab threw the typing
// away without a word.
//
// So the commit moved to the bottom edge and stays there, and it reports the
// form rather than the database: what is unsaved, what is stopping the save, and
// a way back to the last saved state. `settings_view.js` owns the comparison
// that decides all three, because it is the one rule here whose being wrong
// costs somebody their work.
export default function DefaultsPanel({ onOutline }) {
  // `toast(msg)` / `toast.error(msg)` — there is no `.show`, and every call on
  // this page was written as one. The success call threw inside the try, the
  // catch called `.show` again on the way out, and what a developer saw after
  // pressing Save was no confirmation at all and an uncaught TypeError.
  const toast = useToast();
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  // The form as the server last confirmed it. Everything "unsaved" on this page
  // is a comparison against this, so it is replaced only by a response, never by
  // a keystroke.
  const [saved, setSaved] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const searchRef = useRef(null);

  useEffect(() => {
    api
      .userSettings()
      .then((r) => {
        const initial = fromStored(r.catalog, r.values);
        setData(r);
        setForm(initial);
        setSaved(initial);
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
  const dirty = useMemo(
    () => (data && form ? dirtyKeys(data.catalog, form, saved) : []),
    [data, form, saved]
  );
  const groups = useMemo(
    () => (data && form ? visibleGroups(data.catalog, data.groups, form, query) : []),
    [data, form, query]
  );

  const save = useCallback(async () => {
    if (!data || saving || hasErrors(data.catalog, form)) return;
    setSaving(true);
    try {
      const values = overrides(data.catalog, form);
      await api.saveUserSettings(values);
      // Re-read rather than trusting the form: the server is what decides what
      // was stored, and `drifted` is recomputed from it.
      const fresh = await api.userSettings();
      const confirmed = fromStored(fresh.catalog, fresh.values);
      setData(fresh);
      setForm(confirmed);
      setSaved(confirmed);
      toast.success("Defaults saved");
    } catch (e) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  }, [data, form, saving, toast]);

  // Two keyboard habits, both about the same thing: this is a long form, and the
  // hands that fill in long forms do not want to go and find a button.
  useEffect(() => {
    function onKey(e) {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
        return;
      }
      if (meta && e.key.toLowerCase() === "f") {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  // The browser's own "leave site?" prompt, which is the only one that can stop
  // a closed tab. Registered only while there is something to lose — an
  // unconditional handler makes every reload of an untouched page ask.
  useEffect(() => {
    if (dirty.length === 0) return undefined;
    const warn = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty.length]);

  // What the left column lists under "Defaults". Sent up as a plain array and
  // compared by value, because it is rebuilt on every keystroke and a new array
  // identity per render would loop the parent's state forever.
  const outline = useMemo(
    () =>
      groups.map((g) => ({
        id: g.id,
        label: g.label,
        count: g.overridden,
      })),
    [groups]
  );
  const outlineKey = JSON.stringify(outline);
  useEffect(() => {
    onOutline?.(JSON.parse(outlineKey));
  }, [outlineKey, onOutline]);

  if (error) {
    return (
      <Banner tone="error" className="is-block" title="Could not load your settings">
        <BannerDetail>{error}</BannerDetail>
      </Banner>
    );
  }
  if (!data || !form) return <Skeleton variant="row" count={6} />;

  const unseen = new Set(data.unseen);
  const blocked = Object.keys(errors).length > 0;

  async function acknowledge() {
    try {
      await api.markSettingsSeen(data.unseen);
      setData({ ...data, unseen: [] });
    } catch (e) {
      toast.error(e.message);
    }
  }

  function discard() {
    setForm(saved);
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
      </div>

      <div className="settings-search">
        <IconSearch size={14} className="settings-search-icon" />
        <input
          ref={searchRef}
          type="search"
          value={query}
          aria-label="Search settings"
          placeholder="Search settings"
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button
            type="button"
            className="settings-search-clear"
            aria-label="Clear search"
            onClick={() => setQuery("")}
          >
            <IconX size={13} />
          </button>
        )}
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
          actions={
            <Button size="sm" onClick={acknowledge}>
              Got it
            </Button>
          }
        >
          {data.unseen.map((k) => labelOf(data.catalog, k)).join(", ")}. They are
          using this deployment's values until you say otherwise.
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
          {/* Labels, not keys. This banner is read by whoever has to fix it, and
              `early_stop_val_error_share` is not what the field is called
              anywhere they can see. */}
          {data.invalid.map((k) => labelOf(data.catalog, k)).join(", ")} — these
          are being ignored and this deployment's values used instead. Set them
          again to fix it.
        </Banner>
      )}

      {groups.length === 0 ? (
        <div className="settings-empty">
          <p>
            No setting matches <strong>{query}</strong>.
          </p>
          <Button size="sm" onClick={() => setQuery("")}>
            Clear search
          </Button>
        </div>
      ) : (
        groups.map((group) => (
          <FormSection
            key={group.id}
            id={`settings-group-${group.id}`}
            className="settings-group"
            title={group.label}
            description={group.description}
          >
            {group.specs.map((spec) =>
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
        ))
      )}

      {/* The commit, at the edge the eye ends on. It is always rendered rather
          than appearing with the first keystroke: a bar that arrives when you
          type moves the field you are typing in, and a page whose only Save is
          conditional cannot be trusted to have one. */}
      <div className={`settings-bar${dirty.length > 0 ? " is-dirty" : ""}`}>
        <span className="settings-bar-status" role="status">
          {barMessage({
            dirty: dirty.length,
            errors: Object.keys(errors).length,
            overridden: changed.length,
            saving,
          })}
        </span>
        <div className="settings-bar-actions">
          <Button size="sm" onClick={discard} disabled={saving || dirty.length === 0}>
            Discard
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={save}
            loading={saving}
            disabled={blocked || dirty.length === 0}
            icon={<IconCheck size={14} />}
          >
            Save
          </Button>
        </div>
      </div>
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
  const id = `setting-${spec.key}`;

  if (!available) {
    return (
      <Field label={spec.label} className="setting-row">
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
      toast.success(`${spec.label} saved`);
    } catch (e) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    setBusy(true);
    try {
      await api.deleteUserSecret(spec.key);
      await onChanged();
      toast.success(`${spec.label} removed`);
    } catch (e) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Field
      label={spec.label}
      htmlFor={id}
      help={spec.help}
      className={`setting-row${state?.set ? " is-overridden" : ""}`}
      error={
        stale
          ? `Stored for ${bound || "(blank)"}, which is not the address above. It will not be used until you enter it again.`
          : undefined
      }
    >
      <div className="secret-row">
        <input
          id={id}
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
