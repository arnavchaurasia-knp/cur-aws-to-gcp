package jobs

import (
	"encoding/json"
	"fmt"
	"net/http"
	"path/filepath"
	"strconv"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/go-chi/chi/v5"
)

func parseAnalyticsFilter(r *http.Request) db.AnalyticsFilter {
	q := r.URL.Query()
	minBill, _ := strconv.ParseFloat(q.Get("min_bill"), 64)
	return db.AnalyticsFilter{
		Window:   q.Get("window"),
		From:     q.Get("from"),
		To:       q.Get("to"),
		MinBill:  minBill,
		Region:   q.Get("region"),
		Vertical: q.Get("vertical"),
	}
}

// AdminAnalytics returns the portfolio economics summary (Lens A).
func (h *Handler) AdminAnalytics(w http.ResponseWriter, r *http.Request) {
	filter := parseAnalyticsFilter(r)
	stats, err := h.db.GetAnalyticsSummary(filter)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to get analytics summary: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(stats)
}

// AdminAnalyticsComposition returns spend and savings breakdowns by dimension (Lens B).
func (h *Handler) AdminAnalyticsComposition(w http.ResponseWriter, r *http.Request) {
	filter := parseAnalyticsFilter(r)
	dim := r.URL.Query().Get("dimension")
	if dim == "" {
		dim = "service"
	}
	res, err := h.db.GetAnalyticsComposition(dim, filter)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to get composition analytics: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(res)
}

// AdminAnalyticsQuality returns quality, coverage and confidence distribution (Lens C).
func (h *Handler) AdminAnalyticsQuality(w http.ResponseWriter, r *http.Request) {
	filter := parseAnalyticsFilter(r)
	res, err := h.db.GetAnalyticsQuality(filter)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to get quality analytics: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(res)
}

// AdminAnalyticsMethodology returns matrix coverage, SP/RI exposure and findings loop stats (Lens D).
func (h *Handler) AdminAnalyticsMethodology(w http.ResponseWriter, r *http.Request) {
	filter := parseAnalyticsFilter(r)
	res, err := h.db.GetAnalyticsMethodology(filter)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to get methodology analytics: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(res)
}

// AdminAnalyticsRebuild re-derives the rollup for the entire corpus.
func (h *Handler) AdminAnalyticsRebuild(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	actor := "admin"
	if sess != nil && sess.Email != "" {
		actor = sess.Email
	}

	count, err := BackfillJobAnalytics(h.db, h.jobsDir, true)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to rebuild analytics: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}

	_ = h.db.LogAdminAction(actor, "rebuild_analytics", "", fmt.Sprintf("rebuilt %d jobs", count))

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":       "ok",
		"rebuilt_jobs": count,
	})
}

// AdminAnalyticsRefreshJob re-derives the rollup for a single job.
func (h *Handler) AdminAnalyticsRefreshJob(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if id == "" {
		http.Error(w, `{"error":"job id required"}`, http.StatusBadRequest)
		return
	}

	job, err := h.db.GetJob(id)
	if err != nil || job == nil {
		http.Error(w, `{"error":"job not found"}`, http.StatusNotFound)
		return
	}

	jobDir := filepath.Join(h.jobsDir, id)
	rollup, breakdowns, err := ComputeJobAnalytics(job, jobDir, h.db)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to compute analytics: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}

	if err := h.db.UpsertJobAnalytics(rollup, breakdowns); err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to save analytics: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":    "ok",
		"job_id":    id,
		"analytics": rollup,
	})
}
