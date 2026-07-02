export async function copyToClipboard(text: string): Promise<boolean> {
  const value = text.trim();
  if (!value) {
    return false;
  }

  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      /* fall through — common on http://hostname (non-secure context) */
    }
  }

  try {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "0";
    textarea.style.left = "0";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, value.length);
    const copied = document.execCommand("copy");
    document.body.removeChild(textarea);
    return copied;
  } catch {
    return false;
  }
}

export async function copyFromInput(input: HTMLInputElement | null): Promise<boolean> {
  if (!input?.value) {
    return false;
  }

  try {
    input.focus();
    input.select();
    input.setSelectionRange(0, input.value.length);
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(input.value);
        return true;
      } catch {
        /* fall through */
      }
    }
    return document.execCommand("copy");
  } catch {
    return copyToClipboard(input.value);
  }
}
