#!/opt/homebrew/bin/bash
# Search the bundled SKU catalog. Output is TSV:
#   service<TAB>skuId<TAB>resource_group<TAB>usage_type<TAB>regions<TAB>description<TAB>rate_usd<TAB>unit
# Usage:
#   scripts/find-sku.sh --service "Compute Engine" --region asia-southeast1 --resource-group CPU --keyword "N2D"
#   scripts/find-sku.sh --keyword "Hyperdisk Balanced" --region asia-southeast1
#   scripts/find-sku.sh --keyword "external IP" --usage-type OnDemand
# All flags are optional. Without --service, all 110 services are scanned (~1s).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$SKILL_DIR/data"

SERVICE=""; REGION=""; KEYWORD=""; USAGE_TYPE=""; RESOURCE_GROUP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --service)        SERVICE="$2"; shift 2 ;;
    --region)         REGION="$2"; shift 2 ;;
    --keyword)        KEYWORD="$2"; shift 2 ;;
    --usage-type)     USAGE_TYPE="$2"; shift 2 ;;
    --resource-group) RESOURCE_GROUP="$2"; shift 2 ;;
    -h|--help)
      head -12 "$0" | tail -11; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$SERVICE" ]]; then
  SVCID=$(jq -r --arg n "$SERVICE" '.[] | select(.displayName==$n) | .serviceId' "$DATA_DIR/services.json")
  if [[ -z "$SVCID" ]]; then
    echo "Service '$SERVICE' not in allow-list. Available services:" >&2
    jq -r '.[].displayName' "$DATA_DIR/services.json" | sort >&2
    exit 1
  fi
  FILES=("$DATA_DIR/skus/${SVCID}.json.gz")
else
  FILES=("$DATA_DIR/skus/"*.json.gz)
fi

JQ_FILTER='.[]'
[[ -n "$REGION" ]]         && JQ_FILTER+=" | select(.serviceRegions | index(\"$REGION\") or index(\"global\"))"
[[ -n "$KEYWORD" ]]        && JQ_FILTER+=" | select(.description | test(\"$KEYWORD\"; \"i\"))"
[[ -n "$USAGE_TYPE" ]]     && JQ_FILTER+=" | select(.category.usageType==\"$USAGE_TYPE\")"
[[ -n "$RESOURCE_GROUP" ]] && JQ_FILTER+=" | select(.category.resourceGroup==\"$RESOURCE_GROUP\")"

JQ_OUT='. as $sku
| ($sku.pricingInfo[0].pricingExpression.tieredRates // []) as $tr
| (($tr | map(select(((.unitPrice.units // "0") != "0") or ((.unitPrice.nanos // 0) != 0))) | first) // ($tr[0] // {unitPrice:{units:"0",nanos:0}})) as $rate
| [
    $sku.category.serviceDisplayName,
    $sku.skuId,
    $sku.category.resourceGroup,
    $sku.category.usageType,
    ($sku.serviceRegions | join(",")),
    $sku.description,
    (($rate.unitPrice.units // "0" | tonumber) + (($rate.unitPrice.nanos // 0) / 1e9)),
    ($sku.pricingInfo[0].pricingExpression.usageUnit // "")
  ] | @tsv'

for f in "${FILES[@]}"; do
  gzcat "$f" | jq -r "$JQ_FILTER | $JQ_OUT" 2>/dev/null
done
