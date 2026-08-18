# Phase 4.16 – Search Assist Expanded Profile Flow Fix

## Search Assist browser flow

- Clicks the actual Search Assist button once and verifies the Search Assist answer card remains open.
- Does not immediately toggle Search Assist closed by clicking nested text nodes.
- Waits exactly 15 seconds after Search Assist opens before starting the More/expand step.
- Detects the normal `More` button when it is visible.
- Detects the circular/down-arrow expand control used by DDG layouts where `More` is initially hidden.
- Clicks the down-arrow only inside the Search Assist card, avoiding page header dropdowns and settings controls.
- After the arrow expands the card, waits for and clicks `More`.
- After `More`, waits a random 15–25 seconds before extraction.
- Waits for the expanded Company Profile / General Information section to become stable before returning data.
- If Search Assist is genuinely unavailable for a query, the application skips that Search Assist step and continues the normal research workflow instead of blocking the company.

## Exact expanded-profile extraction

- The expanded `Company Profile` / `General Information` section is now the authoritative Search Assist extraction target.
- Table rows are converted into explicit `Label: Value` pairs when DDG renders a real table.
- Div/list layouts are also supported by the existing label-on-next-line parser.
- `Industry` and `Sector` are accepted only from explicit expanded-profile labels.
- The short Search Assist summary is never used to populate Industry or Sector.
- Expanded profile data can populate founded year, employees/company size, headquarters/address, email, website, LinkedIn and phone when available.
- Existing website/LinkedIn verified values remain preferred.

## Reliability

- Expanded-profile detection is based on `Company Profile` + `General Information` rather than the generic Search Assist summary container.
- Added stability checks so partially rendered profile rows are not written to Excel.
- Updated documentation and timing references from the previous 20–25 second wait to the requested 15–25 second random wait.
