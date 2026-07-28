# Semantic Drift Audit

Scanned **10** glossary term(s); **2** had revision history; **1** changed meaning; **1** of those have live consumers.

| Term | Verdict | Dashboards | Charts | Alert |
|---|---|---|---|---|
| Order Total | 🔴 BREAKING | 3 | 12 | YES |
| Revenue by Customer Class | 🟡 CLARIFYING | 3 | 12 | — |

---

## 🔴 Order Total — MEANING CHANGED
Definition changed by **__datahub_system** at 2026-07-28 03:47 UTC (revision v5 → v6).
**Why this classification:**
- inclusion/exclusion of components changed
- aggregation changed
- net/gross basis changed
- calculation boundary changed
- monetary component changed

**What changed:**

```diff
--- v5 (older)
+++ v6 (newer)
@@ -1,18 +1,11 @@
-The total monetary value of an order, including all line items, discounts, and applicable taxes.
-Calculated as the sum of all line item totals for a given order.
+The net monetary value of an order:
+the sum of all line item totals, net of discounts and refunds, EXCLUDING shipping and tax.
+Changed to align order-level revenue with the finance team's net revenue definition.
+Previously this figure was gross of shipping and tax.
 SQL Calculation:
-\- Single order:
-Use column 'order_total' directly
-\- Aggregate total revenue:
-SUM(order_total)
-\- Average order value:
-AVG(order_total)
-\- Count orders:
-COUNT(DISTINCT order_id)
-Example queries:
-\- Total revenue:
-SELECT SUM(order_total) FROM order_entry_db.analytics.order_details
-\- Average order value:
-SELECT AVG(order_total) FROM order_entry_db.analytics.order_details
-\- Revenue by date:
-SELECT order_date, SUM(order_total) FROM order_entry_db.analytics.order_details GROUP BY order_date
+- Single order:
+order_total - shipping_amount - tax_amount
+- Aggregate net revenue:
+SUM(order_total - shipping_amount - tax_amount)
+- Average order value:
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
Definition changed by **__datahub_system** at 2026-07-28 03:47 UTC (revision v1 → v2).
**Why this classification:**
- ownership/context noted

**What changed:**

```diff
--- v1 (older)
+++ v2 (newer)
@@ -2,24 +2,3 @@
 Shows revenue distribution across customer segments.
-SQL Calculation Patterns:
-\- Group by customer_class and aggregate order_total
-\- Formula:
-SUM(order_total) GROUP BY customer_class
-\- Use column 'customer_class' for grouping and 'order_total' for aggregation
-Required SQL structure:
-SELECT
-customer_class,
-SUM(order_total) as total_revenue,
-COUNT(DISTINCT order_id) as order_count,
-AVG(order_total) as avg_order_value
-FROM order_entry_db.analytics.order_details
-GROUP BY customer_class
-ORDER BY total_revenue DESC
-Common aggregations:
-\- Total revenue per class:
-SUM(order_total) GROUP BY customer_class
-\- Order count per class:
-COUNT(DISTINCT order_id) GROUP BY customer_class
-\- Average order value per class:
-AVG(order_total) GROUP BY customer_class
-\- With date filter:
-Add WHERE order_date >= \[date\] before GROUP BY
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

