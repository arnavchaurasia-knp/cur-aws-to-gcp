package auth

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
)

var ErrNoSession = errors.New("no session")

const cookieName = "cur_session"
const cookieTTL = 8 * time.Hour

type Session struct {
	Email string `json:"email"`
	Name  string `json:"name"`
}

// IsAdmin reports whether this session's email is in the admin allow-list.
// adminEmails entries are expected to be lowercased already (parseAdminEmails
// does that at startup). Comparison is case-insensitive on the session side.
func (s *Session) IsAdmin(adminEmails []string) bool {
	if s == nil {
		return false
	}
	email := strings.ToLower(s.Email)
	for _, a := range adminEmails {
		if a == email {
			return true
		}
	}
	return false
}

type SessionManager struct {
	key    []byte
	secure bool
}

func NewSessionManager(hexKey string, secure bool) *SessionManager {
	key, _ := base64.StdEncoding.DecodeString(hexKey)
	if len(key) == 0 {
		key = []byte(hexKey)
	}
	// pad/trim to 32 bytes for AES-256
	k := make([]byte, 32)
	copy(k, key)
	return &SessionManager{key: k, secure: secure}
}

func (sm *SessionManager) Secure() bool { return sm.secure }

func (sm *SessionManager) Set(w http.ResponseWriter, s *Session) error {
	data, err := json.Marshal(s)
	if err != nil {
		return err
	}
	block, err := aes.NewCipher(sm.key)
	if err != nil {
		return err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return err
	}
	ciphertext := gcm.Seal(nonce, nonce, data, nil)
	val := base64.URLEncoding.EncodeToString(ciphertext)
	http.SetCookie(w, &http.Cookie{
		Name:     cookieName,
		Value:    val,
		Path:     "/",
		HttpOnly: true,
		Secure:   sm.secure,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   int(cookieTTL.Seconds()),
	})
	return nil
}

func (sm *SessionManager) Get(r *http.Request) (*Session, error) {
	c, err := r.Cookie(cookieName)
	if err != nil {
		return nil, ErrNoSession
	}
	ciphertext, err := base64.URLEncoding.DecodeString(c.Value)
	if err != nil {
		return nil, ErrNoSession
	}
	block, err := aes.NewCipher(sm.key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	ns := gcm.NonceSize()
	if len(ciphertext) < ns {
		return nil, ErrNoSession
	}
	plain, err := gcm.Open(nil, ciphertext[:ns], ciphertext[ns:], nil)
	if err != nil {
		return nil, ErrNoSession
	}
	var s Session
	if err := json.Unmarshal(plain, &s); err != nil {
		return nil, ErrNoSession
	}
	return &s, nil
}

func (sm *SessionManager) Clear(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{Name: cookieName, MaxAge: -1, Path: "/"})
}
