import re
import sys
import shutil


# ============================================================
# USAGE
# python fix_index.py templates/index.html
# ============================================================

path = sys.argv[1] if len(sys.argv) > 1 else "index.html"

# ------------------------------------------------------------
# Backup
# ------------------------------------------------------------
backup_path = path + ".bak"
shutil.copy2(path, backup_path)

with open(path, "r", encoding="utf-8") as f:
    s = f.read()


def sub(pattern, repl, label, flags=re.S):
    global s

    s, n = re.subn(
        pattern,
        repl,
        s,
        count=1,
        flags=flags
    )

    print(("OK " if n else "SKIP ") + label)


# ============================================================
# 1. PREDICTION FORM PAYLOAD
# ============================================================
PAYLOAD = r'''
const formData = new FormData();

const payload = {
    student_name: studentName,
    subject: subject,

    // Backend names
    hours: hours,
    att: attendance,
    assign: assignment,
    cat: cat,
    pract: practical
};

Object.entries(payload).forEach(([key, value]) => {
    formData.append(key, String(value));
});
'''

sub(
    r"const\s+formData\s*=\s*new\s+FormData\s*\(\s*form\s*\)\s*;",
    PAYLOAD,
    "prediction payload fix"
)


# ============================================================
# 2. THEME FAB CSS
# ============================================================
CSS = r'''
<style id="theme-fab-fix">
.theme-fab {
    position: fixed;
    top: 16px;
    right: 16px;
    z-index: 2000;

    width: 44px;
    height: 44px;

    border-radius: 50%;
    border: 1px solid var(--border-strong);

    background: var(--bg-card);
    color: var(--text-1);

    font-size: 20px;

    cursor: pointer;

    display: flex;
    align-items: center;
    justify-content: center;

    box-shadow: var(--shadow);

    transition:
        transform .2s ease,
        background .2s ease,
        color .2s ease;
}

.theme-fab:hover {
    transform: scale(1.08);
}

@media (max-width: 850px) {
    .mobile-topbar .menu-btn:last-child {
        display: none;
    }

    .theme-fab {
        top: 12px;
        right: 12px;
    }
}
</style>
'''

# Add before </head>
if "theme-fab-fix" not in s:
    sub(
        r"</head>",
        CSS + "\n</head>",
        "theme icon CSS"
    )
else:
    print("SKIP theme icon CSS already exists")


# ============================================================
# 3. THEME BUTTON
# ============================================================
THEME_BUTTON = r'''
<button
    type="button"
    id="themeFab"
    class="theme-fab"
    aria-label="Toggle theme"
    title="Toggle theme">
    🌙
</button>
'''

if 'id="themeFab"' not in s:
    sub(
        r"(<body[^>]*>)",
        r"\1\n" + THEME_BUTTON,
        "theme icon button"
    )
else:
    print("SKIP theme icon button already exists")


# ============================================================
# 4. THEME ICON JAVASCRIPT
# ============================================================
FAB_JS = r'''
const fab = document.getElementById("themeFab");

if (fab) {
    fab.textContent = theme === "light" ? "🌙" : "☀️";
}
'''

# Find localStorage theme line
sub(
    r'(localStorage\.setItem\(\s*[\'"]ml_predictor_theme[\'"]\s*,\s*theme\s*\)\s*;)',
    lambda m: m.group(1) + "\n" + FAB_JS,
    "theme icon JS"
)


# ============================================================
# 5. HISTORY CACHE
# ============================================================
NEW_HISTORY = r'''
let historyLoaded = false;

async function loadHistory(force = false) {

    const container =
        document.getElementById("historyTableContainer");

    if (!container) {
        console.warn("historyTableContainer not found");
        return;
    }

    if (historyLoaded && !force) {
        if (typeof renderHistory === "function") {
            renderHistory(currentHistory);
        }
        return;
    }

    try {

        const response = await fetch(
            "/api/history",
            {
                cache: "no-store",
                headers: {
                    "Cache-Control": "no-cache"
                }
            }
        );

        if (!response.ok) {
            throw new Error(
                "Could not load history."
            );
        }

        const data = await response.json();

        currentHistory =
            Array.isArray(data)
                ? data
                : [];

        historyLoaded = true;

        if (typeof renderHistory === "function") {
            renderHistory(currentHistory);
        }

    } catch (error) {

        console.error(
            "History loading error:",
            error
        );

        container.innerHTML = `
            <div style="
                padding:20px;
                text-align:center;
                color:var(--text-2);
            ">
                <div style="
                    font-size:30px;
                    margin-bottom:8px;
                ">⚠️</div>

                <div>
                    Unable to load prediction history.
                </div>
            </div>
        `;
    }
}
'''

sub(
    r"async\s+function\s+loadHistory\s*\(\s*\)\s*\{.*?(?=function\s+renderHistory\s*\()",
    NEW_HISTORY + "\n",
    "history cache"
)


# ============================================================
# 6. FORCE HISTORY RELOAD AFTER PREDICTION
# ============================================================
sub(
    r"loadHistory\(\);\s*loadPredictionAnalytics\(\);",
    "loadHistory(true);\nloadPredictionAnalytics();",
    "reload after prediction"
)


# ============================================================
# 7. REFRESH BUTTON
# ============================================================
def fix_refresh(match):
    block = match.group(0)

    block = block.replace(
        "loadHistory()",
        "loadHistory(true)"
    )

    return block


sub(
    r"function\s+refreshCurrentData\s*\(\s*\)\s*\{.*?(?=/\*[\s=]*STARTUP)",
    fix_refresh,
    "reload on Refresh button"
)


# ============================================================
# 8. REPORT LINKS
# ============================================================
s = s.replace(
    "/download_report/pdf",
    "/report/pdf"
)

s = s.replace(
    "/download_report/excel",
    "/report/excel"
)

print("OK download links fixed")


# ============================================================
# 9. MAKE SURE PREDICTION ANALYTICS CONTAINER EXISTS
# ============================================================
ANALYTICS_HTML = r'''
<div
    id="predictionAnalytics"
    class="prediction-analytics"
    style="display:none;">
</div>
'''

if 'id="predictionAnalytics"' not in s:

    # Try to put it after coach feedback
    if 'id="coachFeedback"' in s:

        pattern = (
            r'(<[^>]+id=["\']coachFeedback["\'][^>]*>'
            r'.*?</[^>]+>)'
        )

        sub(
            pattern,
            lambda m: m.group(1) + "\n" + ANALYTICS_HTML,
            "prediction analytics container"
        )

    else:

        # Fallback: before closing body
        sub(
            r"</body>",
            ANALYTICS_HTML + "\n</body>",
            "prediction analytics container fallback"
        )

else:
    print(
        "SKIP prediction analytics container already exists"
    )


# ============================================================
# 10. SAVE
# ============================================================
with open(path, "w", encoding="utf-8") as f:
    f.write(s)


print()
print("==============================================")
print("FIX COMPLETED SUCCESSFULLY")
print("==============================================")
print("File   :", path)
print("Backup :", backup_path)
print()
print("IMPORTANT:")
print("Backend prediction fields:")
print("  hours")
print("  att")
print("  assign")
print("  cat")
print("  pract")
print()
print("If prediction still gives an error,")
print("check the /api/predict route in app.py.")
