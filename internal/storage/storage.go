// Package storage provides persistent cloud object storage abstractions for job artifacts,
// supporting Cloudflare R2, AWS S3, Supabase Storage, MinIO, or local-only fallback.
package storage

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	s3types "github.com/aws/aws-sdk-go-v2/service/s3/types"
)

type Config struct {
	Endpoint        string // e.g. "https://<account_id>.r2.cloudflarestorage.com"
	Bucket          string // e.g. "cur-web-jobs"
	AccessKeyID     string
	SecretAccessKey string
	Region          string // e.g. "auto" or "us-east-1"
}

// Client defines the contract for job artifact storage.
type Client interface {
	UploadFile(ctx context.Context, remoteKey, localPath string) error
	DownloadFile(ctx context.Context, remoteKey, localDestPath string) error
	Exists(ctx context.Context, remoteKey string) (bool, error)
	SyncJobToRemote(ctx context.Context, jobID, localJobDir string) error
	RestoreJobFromRemote(ctx context.Context, jobID, localJobDir string) (bool, error)
	IsConfigured() bool
}

// New constructs a Client from Config. If bucket or credentials are unset, returns NoopClient.
func New(ctx context.Context, cfg Config) (Client, error) {
	if cfg.Bucket == "" || (cfg.AccessKeyID == "" && cfg.Endpoint == "") {
		slog.Info("cloud storage not configured; using local filesystem only")
		return &NoopClient{}, nil
	}

	region := cfg.Region
	if region == "" {
		region = "auto"
	}

	customResolver := aws.EndpointResolverWithOptionsFunc(
		func(service, reg string, options ...interface{}) (aws.Endpoint, error) {
			if cfg.Endpoint != "" {
				return aws.Endpoint{
					URL:               cfg.Endpoint,
					SigningRegion:     region,
					HostnameImmutable: true,
				}, nil
			}
			return aws.Endpoint{}, &aws.EndpointNotFoundError{}
		},
	)

	opts := []func(*awsconfig.LoadOptions) error{
		awsconfig.WithRegion(region),
		awsconfig.WithEndpointResolverWithOptions(customResolver),
	}

	if cfg.AccessKeyID != "" && cfg.SecretAccessKey != "" {
		opts = append(opts, awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(cfg.AccessKeyID, cfg.SecretAccessKey, ""),
		))
	}

	awsCfg, err := awsconfig.LoadDefaultConfig(ctx, opts...)
	if err != nil {
		return nil, fmt.Errorf("load aws config: %w", err)
	}

	s3Client := s3.NewFromConfig(awsCfg, func(o *s3.Options) {
		o.UsePathStyle = true
		if cfg.Endpoint != "" {
			o.BaseEndpoint = aws.String(cfg.Endpoint)
		}
	})

	slog.Info("cloud storage initialized", "bucket", cfg.Bucket, "endpoint", cfg.Endpoint)
	return &S3Client{
		client: s3Client,
		bucket: cfg.Bucket,
	}, nil
}

// -----------------------------------------------------------------------------
// S3Client implementation (Cloudflare R2 / AWS S3 / Supabase)
// -----------------------------------------------------------------------------

type S3Client struct {
	client *s3.Client
	bucket string
}

func (s *S3Client) IsConfigured() bool {
	return true
}

func (s *S3Client) UploadFile(ctx context.Context, remoteKey, localPath string) error {
	f, err := os.Open(localPath)
	if err != nil {
		return fmt.Errorf("open file for upload: %w", err)
	}
	defer f.Close()

	_, err = s.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(remoteKey),
		Body:   f,
	})
	if err != nil {
		return fmt.Errorf("s3 put object %s: %w", remoteKey, err)
	}
	return nil
}

func (s *S3Client) DownloadFile(ctx context.Context, remoteKey, localDestPath string) error {
	out, err := s.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(remoteKey),
	})
	if err != nil {
		return fmt.Errorf("s3 get object %s: %w", remoteKey, err)
	}
	defer out.Body.Close()

	if err := os.MkdirAll(filepath.Dir(localDestPath), 0755); err != nil {
		return fmt.Errorf("mkdir for download: %w", err)
	}

	dst, err := os.Create(localDestPath)
	if err != nil {
		return fmt.Errorf("create destination file: %w", err)
	}
	defer dst.Close()

	if _, err := io.Copy(dst, out.Body); err != nil {
		return fmt.Errorf("copy object content: %w", err)
	}
	return nil
}

func (s *S3Client) Exists(ctx context.Context, remoteKey string) (bool, error) {
	_, err := s.client.HeadObject(ctx, &s3.HeadObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(remoteKey),
	})
	if err != nil {
		var notFound *s3types.NotFound
		if errors.As(err, &notFound) {
			return false, nil
		}
		// Some S3-compatible providers return 404 in general error form
		if strings.Contains(err.Error(), "NotFound") || strings.Contains(err.Error(), "404") {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

func (s *S3Client) SyncJobToRemote(ctx context.Context, jobID, localJobDir string) error {
	prefix := fmt.Sprintf("jobs/%s/", jobID)
	var uploadErrors []string

	err := filepath.Walk(localJobDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}

		relPath, err := filepath.Rel(localJobDir, path)
		if err != nil {
			return err
		}

		// Normalize to forward slashes for S3 keys
		key := prefix + filepath.ToSlash(relPath)
		if err := s.UploadFile(ctx, key, path); err != nil {
			uploadErrors = append(uploadErrors, fmt.Sprintf("%s: %v", relPath, err))
		}
		return nil
	})

	if err != nil {
		return fmt.Errorf("walk local job dir: %w", err)
	}
	if len(uploadErrors) > 0 {
		return fmt.Errorf("failed uploading %d files: %s", len(uploadErrors), strings.Join(uploadErrors, "; "))
	}

	slog.Info("job artifacts synced to cloud storage", "job_id", jobID)
	return nil
}

func (s *S3Client) RestoreJobFromRemote(ctx context.Context, jobID, localJobDir string) (bool, error) {
	prefix := fmt.Sprintf("jobs/%s/", jobID)

	paginator := s3.NewListObjectsV2Paginator(s.client, &s3.ListObjectsV2Input{
		Bucket: aws.String(s.bucket),
		Prefix: aws.String(prefix),
	})

	count := 0
	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return false, fmt.Errorf("list objects for job %s: %w", jobID, err)
		}

		for _, item := range page.Contents {
			key := aws.ToString(item.Key)
			relPath := strings.TrimPrefix(key, prefix)
			if relPath == "" || strings.HasSuffix(relPath, "/") {
				continue
			}

			localPath := filepath.Join(localJobDir, filepath.FromSlash(relPath))
			if err := s.DownloadFile(ctx, key, localPath); err != nil {
				return false, fmt.Errorf("download %s: %w", key, err)
			}
			count++
		}
	}

	if count == 0 {
		return false, nil
	}

	slog.Info("job artifacts restored from cloud storage", "job_id", jobID, "files_restored", count)
	return true, nil
}

// -----------------------------------------------------------------------------
// NoopClient implementation (when cloud storage is disabled)
// -----------------------------------------------------------------------------

type NoopClient struct{}

func (n *NoopClient) IsConfigured() bool                                                { return false }
func (n *NoopClient) UploadFile(_ context.Context, _, _ string) error                   { return nil }
func (n *NoopClient) DownloadFile(_ context.Context, _, _ string) error                 { return os.ErrNotExist }
func (n *NoopClient) Exists(_ context.Context, _ string) (bool, error)                  { return false, nil }
func (n *NoopClient) SyncJobToRemote(_ context.Context, _, _ string) error              { return nil }
func (n *NoopClient) RestoreJobFromRemote(_ context.Context, _, _ string) (bool, error) { return false, nil }
