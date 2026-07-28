# Semantic Drift Audit

Scanned **10** glossary term(s); **2** had revision history; **1** changed meaning; **1** of those has live consumers.

| Term                      | Verdict       | Dashboards | Charts | Alert |
|---------------------------|---------------|------------|--------|-------|
| Order Total               | 🔴 BREAKING   | 3          | 12     | YES   |
| Revenue by Customer Class | 🟡 CLARIFYING | 3          | 12     | —     |

---

## 🔴 Order Total — MEANING CHANGED
Definition changed by **`__datahub_system`** at 2026-07-28 03:47 UTC (revision v5 → v6).
**Why this classification:**
- inclusion/exclusion of components changed
- aggregation changed
- net/gross basis changed
- _(+2 related signal(s))_

**What changed:**

```diff
--- v5 (older)
+++ v6 (newer)
@@ -1,18 +1,11 @@
-The total monetary value of an order, including all line items, discounts, and applicable taxes.
-Calculated as the sum of all line item totals for a given order.
+The net monetary value of an order:
+the sum of all line item totals, net of discounts and refunds, EXCLUDING shipping and tax.
  … 2 more lines added
 SQL Calculation:
 - Single order:
-Use column 'order_total' directly
-- Aggregate total revenue:
-SUM(order_total)
+order_total - shipping_amount - tax_amount
+- Aggregate net revenue:
+SUM(order_total - shipping_amount - tax_amount)
 - Average order value:
-AVG(order_total)
-- Count orders:
  … 8 more lines removed
+AVG(order_total - shipping_amount - tax_amount)
```

**Blast radius** — applied to 21 asset(s), which feed 15 consumer surface(s):

- **3 dashboard(s)**
  - Order Entry Dashboard (looker)
  - Order Entry Dashboard (tableau)
  - datahub_order_entries (powerbi)
- **12 chart(s)**
  - Customer Analysis (powerbi)
  - DAX Visual (powerbi)
  - Executive Summary (powerbi)
  - Geographics (powerbi)
  - Order Mode (looker)
  - Order Mode (tableau)
  - Orders By Month (tableau)
  - Orders by Day (looker)
  - …and 4 more
- 29 downstream dataset(s)

**Owners to notify:** 1e0398a3…, EMP006, ORG_BACKEND_ENG, ORG_DATA_PLATFORM, alex@example.com, brock1@example.com, bryan@example.com, jonny1@example.com, jonny2@example.com, kirk@example.com, marty@example.com, sam@example.com

> Every one of those 15 surfaces still renders without error. They now answer a different question than they did before this edit, and nothing in the stack said so.

---

## 🟡 Revenue by Customer Class — definition clarified
Definition changed by **`__datahub_system`** at 2026-07-28 03:47 UTC (revision v1 → v2).
**Why this classification:**
- ownership/context noted

**What changed:**

```diff
--- v1 (older)
+++ v2 (newer)
@@ -2,24 +2,3 @@
 Shows revenue distribution across customer segments.
-SQL Calculation Patterns:
-- Group by customer_class and aggregate order_total
  … 21 more lines removed
+Reviewed and re-published by the data governance team;
+see the analytics handbook for worked examples.
```

**Blast radius** — applied to 17 asset(s), which feed 15 consumer surface(s):

- **3 dashboard(s)**
  - Order Entry Dashboard (looker)
  - Order Entry Dashboard (tableau)
  - datahub_order_entries (powerbi)
- **12 chart(s)**
  - Customer Analysis (powerbi)
  - DAX Visual (powerbi)
  - Executive Summary (powerbi)
  - Geographics (powerbi)
  - Order Mode (looker)
  - Order Mode (tableau)
  - Orders By Month (tableau)
  - Orders by Day (looker)
  - …and 4 more
- 25 downstream dataset(s)

> No action needed — the computation is unchanged.

---

