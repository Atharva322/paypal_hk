const STYLE_ID = "pay-guard-styles";

// Fixed light colours: the dialog sits on any merchant's page, so it cannot inherit theirs.
const CSS = `
.pg-dialog {
  box-sizing: border-box;
  width: min(32rem, calc(100vw - 2rem));
  max-height: calc(100dvh - 2rem);
  overflow-y: auto;
  padding: 1.5rem;
  border: 2px solid #1f1f1f;
  border-radius: 8px;
  background: #ffffff;
  color: #1f1f1f;
  font: 1rem/1.5 system-ui, sans-serif;
}
.pg-dialog::backdrop { background: rgb(0 0 0 / 0.6); }
.pg-dialog h2 { margin: 0 0 0.75rem; font-size: 1.5rem; line-height: 1.25; }
.pg-dialog h2:focus-visible,
.pg-dialog button:focus-visible,
.pg-dialog input:focus-visible { outline: 3px solid #0b57b0; outline-offset: 2px; }
.pg-reasons { margin: 0 0 1rem; padding: 0.75rem 0.75rem 0.75rem 2rem; border: 2px solid #1f1f1f; }
.pg-details { margin: 0 0 1rem; }
.pg-details dt { font-weight: 700; }
.pg-details dd { margin: 0 0 0.5rem; overflow-wrap: anywhere; }
.pg-details ul { margin: 0; padding-left: 1.25rem; }
.pg-pause { margin: 0 0 0.5rem; font-weight: 700; }
.pg-field { display: block; margin-bottom: 1rem; font-weight: 700; }
.pg-field input {
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-height: 44px;
  margin-top: 0.25rem;
  padding: 0.5rem;
  border: 2px solid #1f1f1f;
  border-radius: 4px;
  font: inherit;
}
.pg-message { min-height: 1.5rem; margin: 0 0 0.5rem; font-weight: 700; }
.pg-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.pg-actions button {
  flex: 1 1 10rem;
  min-height: 44px;
  padding: 0.5rem 1rem;
  border: 2px solid #1f1f1f;
  border-radius: 4px;
  background: #ffffff;
  color: #1f1f1f;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.pg-actions .pg-confirm { border-color: #0b57b0; background: #0b57b0; color: #ffffff; }
.pg-actions .pg-confirm[aria-disabled="true"] {
  border-color: #1f1f1f;
  background: #ffffff;
  color: #1f1f1f;
  cursor: not-allowed;
}
.pg-visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
`;

export function injectStyles(): void {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.append(style);
}
