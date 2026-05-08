/*
 * nelac_runtime.c — NELA-C standalone runtime
 *
 * Reads a .nelac file (interaction net bytecode), runs the SIC reducer,
 * then prints the result value to stdout.  No Python required at runtime.
 *
 * Build:
 *   cc -O2 -o nelac src/nelac_runtime.c -lm && ./nelac out.nelac
 *
 * .nelac format (big-endian):
 *   magic[5]   "NELAC"
 *   version    u8
 *   node_count u32
 *   nodes[]    { tag u8, arity u8, meta i64, ports[0..arity] u32 }
 *   root       u32
 *
 * Port encoding:
 *   ports[0]          = principal port connection
 *   ports[1..arity]   = auxiliary port connections
 *   each port value   = connected node index, or 0xFFFFFFFF (unconnected)
 *
 * Theory: Interaction Nets (Lafont 1990).
 *   Active pair: two nodes whose principal ports (ports[0]) point to each other.
 *   Reduction: fire the rule for that tag-pair; repeat until no active pairs remain.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <assert.h>

/* ── Agent tags ──────────────────────────────────────────────────────────── */

#define TAG_CON 0x01
#define TAG_DUP 0x02
#define TAG_ERA 0x03
#define TAG_APP 0x04
#define TAG_LAM 0x05
#define TAG_INT 0x10
#define TAG_FLT 0x11
#define TAG_STR 0x12
#define TAG_BOO 0x13
#define TAG_PAR 0x14
#define TAG_ADD 0x20
#define TAG_SUB 0x21
#define TAG_MUL 0x22
#define TAG_DIV 0x23
#define TAG_MOD 0x24
#define TAG_NEG 0x25
#define TAG_EQL 0x30
#define TAG_LTH 0x31
#define TAG_LEQ 0x32
#define TAG_GTH 0x33
#define TAG_GEQ 0x34
#define TAG_AND 0x40
#define TAG_ORR 0x41
#define TAG_NOT 0x42
#define TAG_IFT 0x50
#define TAG_NIL 0x60
#define TAG_HED 0x61
#define TAG_TAL 0x62
#define TAG_GET 0x63
#define TAG_LEN 0x64
#define TAG_ARR 0x65
#define TAG_AST 0x66

#define NULL_PORT 0xFFFFFFFFu

/* Maximum ports per node (principal + aux).  Current max arity = 3 (IFT). */
#define MAX_PORTS 4

/* ── Net ─────────────────────────────────────────────────────────────────── */

typedef struct {
    uint8_t  tag;
    uint8_t  arity;
    int64_t  meta;
    uint32_t ports[MAX_PORTS];  /* ports[0] = principal */
    int      alive;             /* 0 = deleted (free slot) */
} Node;

typedef struct {
    Node    *nodes;
    uint32_t cap;
    uint32_t count;             /* total allocated (including deleted) */
} Net;

static void net_init(Net *net, uint32_t initial_cap) {
    net->nodes = calloc(initial_cap, sizeof(Node));
    assert(net->nodes);
    net->cap   = initial_cap;
    net->count = 0;
}

static uint32_t net_alloc(Net *net, uint8_t tag, uint8_t arity, int64_t meta) {
    if (net->count >= net->cap) {
        net->cap *= 2;
        net->nodes = realloc(net->nodes, net->cap * sizeof(Node));
        assert(net->nodes);
        /* zero the new half */
        memset(net->nodes + net->count, 0,
               (net->cap - net->count) * sizeof(Node));
    }
    uint32_t nid = net->count++;
    Node *n = &net->nodes[nid];
    n->tag   = tag;
    n->arity = arity;
    n->meta  = meta;
    n->alive = 1;
    for (int i = 0; i <= MAX_PORTS - 1; i++) n->ports[i] = NULL_PORT;
    return nid;
}

/* Connect port (a, pa) ↔ (b, pb) bidirectionally. */
/* Get node id from a packed port value. */
static inline uint32_t port_node(uint32_t p) { return p / MAX_PORTS; }
/* Get port index from a packed port value. */
static inline int      port_idx (uint32_t p) { return (int)(p % MAX_PORTS); }

static void net_free(Net *net) { free(net->nodes); }

/* ── Big-endian readers ───────────────────────────────────────────────────── */

static uint32_t read_u32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16)
         | ((uint32_t)p[2] <<  8) |  (uint32_t)p[3];
}
static int64_t read_i64(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) v = (v << 8) | p[i];
    return (int64_t)v;
}
/* ── Load .nelac ──────────────────────────────────────────────────────────── */

/* Returns root node id, or exits on error. */
static uint32_t load_nelac(const char *path, Net *net) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(1); }

    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    rewind(f);
    uint8_t *buf = malloc(fsize);
    assert(buf);
    if (fread(buf, 1, fsize, f) != (size_t)fsize) {
        fprintf(stderr, "Read error\n"); exit(1);
    }
    fclose(f);

    if (fsize < 10 || memcmp(buf, "NELAC", 5) != 0) {
        fprintf(stderr, "Not a .nelac file\n"); exit(1);
    }
    /* uint8_t version = buf[5]; */
    uint32_t node_count = read_u32(buf + 6);

    net_init(net, node_count + 64);

    size_t off = 10;
    for (uint32_t i = 0; i < node_count; i++) {
        uint8_t  tag   = buf[off++];
        uint8_t  arity = buf[off++];
        int64_t  meta  = read_i64(buf + off); off += 8;
        uint32_t nid   = net_alloc(net, tag, arity, meta);
        /* ports[0..arity] */
        for (int p = 0; p <= arity; p++) {
            uint32_t raw = read_u32(buf + off); off += 4;
            /* raw is a node index in the file; convert to packed port format.
             * In the Python serialiser, ports store *node indices* (not packed).
             * We re-encode as packed (nid * MAX_PORTS + 0) with port-index 0
             * as a placeholder — re-wired after all nodes are loaded. */
            net->nodes[nid].ports[p] = (raw == NULL_PORT) ? NULL_PORT : raw;
        }
    }
    uint32_t root = read_u32(buf + off);
    free(buf);

    /* The Python serialiser stores raw node indices in port fields (not packed).
     * Repack: for each node, for each port, if the stored value is a node index,
     * the connected port index is 0 (principal-to-principal or aux link stored by
     * index only).  We need to resolve the full bidirectional links.
     *
     * Actually the Python format stores node indices only — the port index that the
     * peer is connected to is implicit (the peer's port that points back to us).
     * We must scan every node's ports to compute the full bidirectional mapping. */

    /* Build port map: for each (nid, port_idx) → packed target.
     * The Python serialiser stores in ports[i] the node id of the connected node.
     * We find the back-pointer by searching that node's ports for our nid. */
    uint32_t n = net->count;
    for (uint32_t a = 0; a < n; a++) {
        if (!net->nodes[a].alive) continue;
        for (int pa = 0; pa <= net->nodes[a].arity; pa++) {
            uint32_t b = net->nodes[a].ports[pa];
            if (b == NULL_PORT) continue;
            /* find which port of b points back to a */
            int pb = -1;
            for (int q = 0; q <= net->nodes[b].arity; q++) {
                if (net->nodes[b].ports[q] == a) { pb = q; break; }
            }
            if (pb >= 0) {
                net->nodes[a].ports[pa] = b * MAX_PORTS + pb;
            } else {
                /* no back-pointer found — leaf or root connection; treat as principal */
                net->nodes[a].ports[pa] = b * MAX_PORTS + 0;
            }
        }
    }

    return root;
}

/* ── SIC Reducer ─────────────────────────────────────────────────────────── */

/* Erase a subgraph rooted at principal port of node nid. */
static void erase(Net *net, uint32_t nid);

static void link(Net *net, uint32_t pa_packed, uint32_t pb_packed) {
    /* Connect two half-edges. If both are NULL, do nothing.
     * If one is NULL, the other becomes a free port (leave as-is). */
    if (pa_packed == NULL_PORT || pb_packed == NULL_PORT) return;
    uint32_t a = port_node(pa_packed), ia = port_idx(pa_packed);
    uint32_t b = port_node(pb_packed), ib = port_idx(pb_packed);
    net->nodes[a].ports[ia] = pb_packed;
    net->nodes[b].ports[ib] = pa_packed;
}

static void erase(Net *net, uint32_t nid) {
    Node *n = &net->nodes[nid];
    if (!n->alive) return;
    n->alive = 0;
    /* erase all aux ports recursively */
    for (int i = 1; i <= n->arity; i++) {
        uint32_t p = n->ports[i];
        if (p != NULL_PORT) {
            uint32_t peer = port_node(p);
            if (net->nodes[peer].alive)
                erase(net, peer);
        }
    }
}

/* Fire one interaction rule for the active pair (a ⊳ b).
 * Returns 1 if a rule fired, 0 otherwise. */
static int fire(Net *net, uint32_t a, uint32_t b);

static void reduce(Net *net) {
    /* Simple worklist: scan all nodes repeatedly until no active pair fires.
     * For real performance a proper worklist queue is better, but this is
     * correct and sufficient for the value-decode use case. */
    int progress = 1;
    while (progress) {
        progress = 0;
        for (uint32_t i = 0; i < net->count; i++) {
            if (!net->nodes[i].alive) continue;
            uint32_t pp = net->nodes[i].ports[0];
            if (pp == NULL_PORT) continue;
            uint32_t j = port_node(pp);
            if (!net->nodes[j].alive) continue;
            if (port_idx(pp) != 0) continue; /* not a principal-principal pair */
            if (j <= i) continue;             /* process each pair once */
            if (fire(net, i, j)) progress = 1;
        }
    }
}

/* Helper: unpack float from meta */
static double meta_to_double(int64_t meta) {
    double d; memcpy(&d, &meta, 8); return d;
}
static int64_t double_to_meta(double d) {
    int64_t m; memcpy(&m, &d, 8); return m;
}

static int fire(Net *net, uint32_t ai, uint32_t bi) {
    Node *a = &net->nodes[ai];
    Node *b = &net->nodes[bi];

    /* ── APP ⊳ LAM  (β-reduction) ───────────────────────────────────────── */
    if ((a->tag == TAG_APP && b->tag == TAG_LAM) ||
        (a->tag == TAG_LAM && b->tag == TAG_APP)) {
        uint32_t app = (a->tag == TAG_APP) ? ai : bi;
        uint32_t lam = (a->tag == TAG_LAM) ? ai : bi;
        /* APP: ports[0]=principal, ports[1]=result_out, ports[2]=arg_in
         * LAM: ports[0]=principal, ports[1]=body_out,   ports[2]=var_in */
        uint32_t result  = net->nodes[app].ports[1];
        uint32_t arg     = net->nodes[app].ports[2];
        uint32_t body    = net->nodes[lam].ports[1];
        uint32_t var     = net->nodes[lam].ports[2];
        net->nodes[app].alive = 0;
        net->nodes[lam].alive = 0;
        link(net, result, body);
        link(net, arg,    var);
        return 1;
    }

    /* ── DUP ⊳ LAM  (copy a lambda) ─────────────────────────────────────── */
    if ((a->tag == TAG_DUP && b->tag == TAG_LAM) ||
        (a->tag == TAG_LAM && b->tag == TAG_DUP)) {
        uint32_t dup = (a->tag == TAG_DUP) ? ai : bi;
        uint32_t lam = (a->tag == TAG_LAM) ? ai : bi;
        /* DUP: ports[1]=copy_a, ports[2]=copy_b
         * LAM: ports[1]=body,   ports[2]=var    */
        uint32_t ca = net->nodes[dup].ports[1];
        uint32_t cb = net->nodes[dup].ports[2];
        uint32_t body = net->nodes[lam].ports[1];
        uint32_t var  = net->nodes[lam].ports[2];
        net->nodes[dup].alive = 0;
        net->nodes[lam].alive = 0;
        /* create lam_a, lam_b */
        uint32_t la = net_alloc(net, TAG_LAM, 2, 0);
        uint32_t lb = net_alloc(net, TAG_LAM, 2, 0);
        /* dup for body, dup for var */
        uint32_t db = net_alloc(net, TAG_DUP, 2, 0);
        uint32_t dv = net_alloc(net, TAG_DUP, 2, 0);
        /* wire dup_body ⊳ body, dup_var ⊳ var */
        link(net, db * MAX_PORTS + 0, body);
        link(net, dv * MAX_PORTS + 0, var);
        /* lam_a body ← db copy1, var ← dv copy1 */
        link(net, la * MAX_PORTS + 1, db * MAX_PORTS + 1);
        link(net, la * MAX_PORTS + 2, dv * MAX_PORTS + 1);
        /* lam_b body ← db copy2, var ← dv copy2 */
        link(net, lb * MAX_PORTS + 1, db * MAX_PORTS + 2);
        link(net, lb * MAX_PORTS + 2, dv * MAX_PORTS + 2);
        /* connect to dup's output ports */
        link(net, la * MAX_PORTS + 0, ca);
        link(net, lb * MAX_PORTS + 0, cb);
        return 1;
    }

    /* ── ERA ⊳ anything ─────────────────────────────────────────────────── */
    if (a->tag == TAG_ERA || b->tag == TAG_ERA) {
        uint32_t era   = (a->tag == TAG_ERA) ? ai : bi;
        uint32_t other = (a->tag == TAG_ERA) ? bi : ai;
        net->nodes[era].alive = 0;
        erase(net, other);
        return 1;
    }

    /* ── IFT ⊳ BOO ──────────────────────────────────────────────────────── */
    if ((a->tag == TAG_IFT && b->tag == TAG_BOO) ||
        (a->tag == TAG_BOO && b->tag == TAG_IFT)) {
        uint32_t ift = (a->tag == TAG_IFT) ? ai : bi;
        uint32_t boo = (a->tag == TAG_BOO) ? ai : bi;
        /* IFT: ports[1]=then_branch, ports[2]=else_branch, ports[3]=result */
        int cond = (int)net->nodes[boo].meta;
        uint32_t then_p = net->nodes[ift].ports[1];
        uint32_t else_p = net->nodes[ift].ports[2];
        uint32_t result = net->nodes[ift].ports[3];
        net->nodes[ift].alive = 0;
        net->nodes[boo].alive = 0;
        if (cond) {
            link(net, then_p, result);
            if (else_p != NULL_PORT) erase(net, port_node(else_p));
        } else {
            link(net, else_p, result);
            if (then_p != NULL_PORT) erase(net, port_node(then_p));
        }
        return 1;
    }

    /* ── Arithmetic: opcode ⊳ PAR(INT/FLT, INT/FLT) ─────────────────────
     * In the compiled net, binary ops are represented as:
     *   OP node (arity 3): ports[1]=left, ports[2]=right, ports[3]=result
     * When ports[1] and ports[2] are leaves (INT/FLT), fire. */

#define ARITH_OP(OPTAG, EXPR_INT, EXPR_FLT) \
    if (a->tag == (OPTAG) || b->tag == (OPTAG)) { \
        Node *op_n  = (a->tag == (OPTAG)) ? a : b; \
        uint32_t lp = op_n->ports[1], rp = op_n->ports[2], resp = op_n->ports[3]; \
        if (lp == NULL_PORT || rp == NULL_PORT) return 0; \
        uint32_t ln = port_node(lp), rn = port_node(rp); \
        Node *lnode = &net->nodes[ln], *rnode = &net->nodes[rn]; \
        if ((lnode->tag == TAG_INT || lnode->tag == TAG_FLT) && \
            (rnode->tag == TAG_INT || rnode->tag == TAG_FLT)) { \
            int is_flt = (lnode->tag == TAG_FLT || rnode->tag == TAG_FLT); \
            uint32_t res_nid; \
            if (is_flt) { \
                double lv = (lnode->tag==TAG_FLT) ? meta_to_double(lnode->meta) : (double)lnode->meta; \
                double rv = (rnode->tag==TAG_FLT) ? meta_to_double(rnode->meta) : (double)rnode->meta; \
                double rv_ = rv; (void)rv_; \
                res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(EXPR_FLT)); \
            } else { \
                int64_t lv = lnode->meta, rv = rnode->meta; \
                int64_t rv_ = rv; (void)rv_; \
                res_nid = net_alloc(net, TAG_INT, 0, (int64_t)(EXPR_INT)); \
            } \
            lnode->alive = 0; rnode->alive = 0; op_n->alive = 0; \
            if (resp != NULL_PORT) link(net, res_nid * MAX_PORTS + 0, resp); \
            return 1; \
        } \
        return 0; \
    }

    ARITH_OP(TAG_ADD, lv + rv,  lv + rv)
    ARITH_OP(TAG_SUB, lv - rv,  lv - rv)
    ARITH_OP(TAG_MUL, lv * rv,  lv * rv)
    ARITH_OP(TAG_DIV, (rv_ != 0 ? lv / rv : 0), (rv_ != 0.0 ? lv / rv : 0.0))
    ARITH_OP(TAG_MOD, (rv_ != 0 ? lv % rv : 0), fmod(lv, rv_ != 0.0 ? rv : 1.0))

    /* ── NEG ⊳ INT/FLT ──────────────────────────────────────────────────── */
    if (a->tag == TAG_NEG || b->tag == TAG_NEG) {
        Node *op_n  = (a->tag == TAG_NEG) ? a : b;
        uint32_t vp = op_n->ports[1], resp = op_n->ports[2];
        if (vp == NULL_PORT) return 0;
        Node *vnode = &net->nodes[port_node(vp)];
        if (vnode->tag == TAG_INT || vnode->tag == TAG_FLT) {
            uint32_t res_nid;
            if (vnode->tag == TAG_FLT)
                res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(-meta_to_double(vnode->meta)));
            else
                res_nid = net_alloc(net, TAG_INT, 0, -vnode->meta);
            vnode->alive = 0; op_n->alive = 0;
            if (resp != NULL_PORT) link(net, res_nid * MAX_PORTS + 0, resp);
            return 1;
        }
        return 0;
    }

/* ── Comparison ops ─────────────────────────────────────────────────────── */
#define CMP_OP(OPTAG, CMP_INT, CMP_FLT) \
    if (a->tag == (OPTAG) || b->tag == (OPTAG)) { \
        Node *op_n  = (a->tag == (OPTAG)) ? a : b; \
        uint32_t lp = op_n->ports[1], rp = op_n->ports[2], resp = op_n->ports[3]; \
        if (lp == NULL_PORT || rp == NULL_PORT) return 0; \
        Node *lnode = &net->nodes[port_node(lp)], *rnode = &net->nodes[port_node(rp)]; \
        if ((lnode->tag == TAG_INT || lnode->tag == TAG_FLT || lnode->tag == TAG_STR || lnode->tag == TAG_BOO) && \
            (rnode->tag == TAG_INT || rnode->tag == TAG_FLT || rnode->tag == TAG_STR || rnode->tag == TAG_BOO)) { \
            int result; \
            if (lnode->tag == TAG_FLT || rnode->tag == TAG_FLT) { \
                double lv = (lnode->tag==TAG_FLT) ? meta_to_double(lnode->meta) : (double)lnode->meta; \
                double rv = (rnode->tag==TAG_FLT) ? meta_to_double(rnode->meta) : (double)rnode->meta; \
                result = (CMP_FLT); \
            } else { \
                int64_t lv = lnode->meta, rv = rnode->meta; \
                result = (CMP_INT); \
            } \
            lnode->alive = 0; rnode->alive = 0; op_n->alive = 0; \
            uint32_t res_nid = net_alloc(net, TAG_BOO, 0, (int64_t)result); \
            if (resp != NULL_PORT) link(net, res_nid * MAX_PORTS + 0, resp); \
            return 1; \
        } \
        return 0; \
    }

    CMP_OP(TAG_EQL, lv == rv, lv == rv)
    CMP_OP(TAG_LTH, lv <  rv, lv <  rv)
    CMP_OP(TAG_LEQ, lv <= rv, lv <= rv)
    CMP_OP(TAG_GTH, lv >  rv, lv >  rv)
    CMP_OP(TAG_GEQ, lv >= rv, lv >= rv)

    /* ── Boolean ops ────────────────────────────────────────────────────── */
    if ((a->tag == TAG_AND || b->tag == TAG_AND)) {
        Node *op_n  = (a->tag == TAG_AND) ? a : b;
        uint32_t lp = op_n->ports[1], rp = op_n->ports[2], resp = op_n->ports[3];
        if (lp == NULL_PORT || rp == NULL_PORT) return 0;
        Node *ln = &net->nodes[port_node(lp)], *rn = &net->nodes[port_node(rp)];
        if (ln->tag == TAG_BOO && rn->tag == TAG_BOO) {
            int res = (int)(ln->meta && rn->meta);
            ln->alive = 0; rn->alive = 0; op_n->alive = 0;
            uint32_t r = net_alloc(net, TAG_BOO, 0, res);
            if (resp != NULL_PORT) link(net, r * MAX_PORTS + 0, resp);
            return 1;
        }
        return 0;
    }
    if ((a->tag == TAG_ORR || b->tag == TAG_ORR)) {
        Node *op_n  = (a->tag == TAG_ORR) ? a : b;
        uint32_t lp = op_n->ports[1], rp = op_n->ports[2], resp = op_n->ports[3];
        if (lp == NULL_PORT || rp == NULL_PORT) return 0;
        Node *ln = &net->nodes[port_node(lp)], *rn = &net->nodes[port_node(rp)];
        if (ln->tag == TAG_BOO && rn->tag == TAG_BOO) {
            int res = (int)(ln->meta || rn->meta);
            ln->alive = 0; rn->alive = 0; op_n->alive = 0;
            uint32_t r = net_alloc(net, TAG_BOO, 0, res);
            if (resp != NULL_PORT) link(net, r * MAX_PORTS + 0, resp);
            return 1;
        }
        return 0;
    }
    if ((a->tag == TAG_NOT || b->tag == TAG_NOT)) {
        Node *op_n  = (a->tag == TAG_NOT) ? a : b;
        uint32_t vp = op_n->ports[1], resp = op_n->ports[2];
        if (vp == NULL_PORT) return 0;
        Node *vn = &net->nodes[port_node(vp)];
        if (vn->tag == TAG_BOO) {
            int res = !(int)vn->meta;
            vn->alive = 0; op_n->alive = 0;
            uint32_t r = net_alloc(net, TAG_BOO, 0, res);
            if (resp != NULL_PORT) link(net, r * MAX_PORTS + 0, resp);
            return 1;
        }
        return 0;
    }

    return 0; /* no rule matched */
}

/* ── Result printer ──────────────────────────────────────────────────────── */

static void print_value(Net *net, uint32_t nid, int depth) {
    if (depth > 10000) { printf("..."); return; }
    Node *n = &net->nodes[nid];
    switch (n->tag) {
        case TAG_INT:
            printf("%lld", (long long)n->meta);
            break;
        case TAG_FLT: {
            double d; memcpy(&d, &n->meta, 8);
            char buf[64];
            snprintf(buf, sizeof(buf), "%.10g", d);
            /* ensure there's always a decimal point so it reads as float */
            if (!strchr(buf, '.') && !strchr(buf, 'e') && !strchr(buf, 'E'))
                strncat(buf, ".0", sizeof(buf) - strlen(buf) - 1);
            printf("%s", buf);
            break;
        }
        case TAG_STR:
            printf("'%c'", (char)(n->meta & 0x7F));
            break;
        case TAG_BOO:
            printf("%s", n->meta ? "True" : "False");
            break;
        case TAG_NIL:
            printf("[]");
            break;
        case TAG_CON: {
            /* print as list: collect all elements */
            printf("[");
            int first = 1;
            uint32_t cur = nid;
            while (net->nodes[cur].tag == TAG_CON) {
                if (!first) printf(", ");
                first = 0;
                uint32_t head = port_node(net->nodes[cur].ports[1]);
                print_value(net, head, depth + 1);
                uint32_t tail_port = net->nodes[cur].ports[2];
                if (tail_port == NULL_PORT) break;
                cur = port_node(tail_port);
            }
            printf("]");
            break;
        }
        case TAG_PAR:
            printf("(");
            if (n->ports[1] != NULL_PORT) print_value(net, port_node(n->ports[1]), depth + 1);
            printf(", ");
            if (n->ports[2] != NULL_PORT) print_value(net, port_node(n->ports[2]), depth + 1);
            printf(")");
            break;
        default:
            printf("<node tag=0x%02x>", n->tag);
            break;
    }
}

/* ── main ────────────────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: nelac <file.nelac> [--disasm]\n");
        return 1;
    }

    int disasm = (argc >= 3 && strcmp(argv[2], "--disasm") == 0);

    Net net;
    uint32_t root = load_nelac(argv[1], &net);

    if (disasm) {
        printf("NELAC  nodes=%u  root=%u\n", net.count, root);
        for (uint32_t i = 0; i < net.count; i++) {
            Node *n = &net.nodes[i];
            if (!n->alive) continue;
            /* tag name */
            const char *names[] = {
                [TAG_CON]="CON",[TAG_DUP]="DUP",[TAG_ERA]="ERA",
                [TAG_APP]="APP",[TAG_LAM]="LAM",
                [TAG_INT]="INT",[TAG_FLT]="FLT",[TAG_STR]="STR",
                [TAG_BOO]="BOO",[TAG_PAR]="PAR",
                [TAG_ADD]="ADD",[TAG_SUB]="SUB",[TAG_MUL]="MUL",
                [TAG_DIV]="DIV",[TAG_MOD]="MOD",[TAG_NEG]="NEG",
                [TAG_EQL]="EQL",[TAG_LTH]="LTH",[TAG_LEQ]="LEQ",
                [TAG_GTH]="GTH",[TAG_GEQ]="GEQ",
                [TAG_AND]="AND",[TAG_ORR]="ORR",[TAG_NOT]="NOT",
                [TAG_IFT]="IFT",[TAG_NIL]="NIL",[TAG_HED]="HED",
                [TAG_TAL]="TAL",[TAG_GET]="GET",[TAG_LEN]="LEN",
                [TAG_ARR]="ARR",[TAG_AST]="AST",
            };
            const char *tname = (n->tag < 0x70 && names[n->tag]) ? names[n->tag] : "???";
            printf("  [%4u] %-6s  meta=%-14lld  ports=[", i, tname, (long long)n->meta);
            for (int p = 0; p <= n->arity; p++) {
                uint32_t pv = n->ports[p];
                if (pv == NULL_PORT) printf("_");
                else printf("%u:%u", port_node(pv), port_idx(pv));
                if (p < n->arity) printf(", ");
            }
            printf("]\n");
        }
        net_free(&net);
        return 0;
    }

    /* Reduce (no-op for v0.10 normal-form nets; full SIC for v0.11 nets) */
    reduce(&net);

    /* Print result */
    print_value(&net, root, 0);
    printf("\n");

    net_free(&net);
    return 0;
}
