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
#ifdef __APPLE__
#  include <termios.h>
#  include <unistd.h>
#else
#  include <termios.h>
#  include <unistd.h>
#endif

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
#define TAG_VAR   0x06  /* wire placeholder: arity=1, ports[1]=peer */
#define TAG_FIX   0x07  /* fixed-point: arity=1, ports[1]=body LAM */
#define TAG_IOT   0x08  /* IOToken leaf: arity=0 */
#define TAG_IOKEY 0x09  /* io_key:   arity=2  p[1]=result_pair p[2]=_ */
#define TAG_IOPRT 0x0A  /* io_print: arity=3  p[1]=frame p[2]=_ p[3]=token_out */
#define TAG_MAT   0x0B  /* match node: meta=ncases, arity=1+ncases */
#define TAG_FST   0x0C  /* fst: arity=2  p[1]=pair_in p[2]=result */
#define TAG_SND   0x0D  /* snd: arity=2  p[1]=pair_in p[2]=result */
#define TAG_FREF  0x0E  /* function reference: arity=0, meta=fn_id; fires by deep-copy */
#define TAG_IFT 0x50
#define TAG_NIL 0x60
#define TAG_HED 0x61
#define TAG_TAL 0x62
#define TAG_GET 0x63
#define TAG_LEN 0x64
#define TAG_ARR 0x65
#define TAG_AST 0x66

/* Math/list unary ops (same arity=2 pattern as HED/TAL) */
#define TAG_SIN   0xE0
#define TAG_COS   0xE1
#define TAG_SQRT  0xE2
#define TAG_FLOOR 0xE3
#define TAG_CEIL  0xE4
#define TAG_ROUND 0xE5
#define TAG_ABSS  0xE6
#define TAG_ORD   0xE7
#define TAG_CHR   0xE8

/* Builtin list ops with 2-3 operands */
#define TAG_APPEND 0xF0
#define TAG_FILT_LE 0xF1
#define TAG_FILT_GT 0xF2
#define TAG_FILT_LT 0xF3
#define TAG_FILT_GE 0xF4
#define TAG_FILT_EQ 0xF5
#define TAG_TAKE   0xF6
#define TAG_DROP   0xF7

#define NULL_PORT 0xFFFFFFFFu

/* Maximum ports per node (principal + 4 aux = 5 total, for AST/IOPRT). */
#define MAX_PORTS 5

/* ── Net ─────────────────────────────────────────────────────────────────── */

typedef struct {
    uint8_t  tag;
    uint8_t  arity;
    uint8_t  alive;             /* 0 = deleted (free slot) */
    uint8_t  _pad;
    int64_t  meta;
    uint32_t ports[MAX_PORTS];  /* ports[0] = principal */
} Node;

typedef struct {
    Node    *nodes;
    uint32_t cap;
    uint32_t count;             /* high-water mark: next fresh slot index */
    uint32_t live;              /* number of currently alive nodes */
    /* free-list: dead slots available for reuse (stable node IDs!) */
    uint32_t *free_list;
    uint32_t  free_head;        /* top of free stack (free_list[0..free_head-1]) */
    uint32_t  free_cap;
} Net;

#define NET_MAX_NODES  (1u << 24)   /* 16M node hard cap */

static void net_init(Net *net, uint32_t initial_cap) {
    if (initial_cap < 256) initial_cap = 256;
    net->nodes     = (Node*)calloc(initial_cap, sizeof(Node));
    assert(net->nodes);
    net->cap       = initial_cap;
    net->count     = 0;
    net->live      = 0;
    net->free_list = (uint32_t*)malloc(1024 * sizeof(uint32_t));
    assert(net->free_list);
    net->free_head = 0;
    net->free_cap  = 1024;
}

static uint32_t net_alloc(Net *net, uint8_t tag, uint8_t arity, int64_t meta) {
    uint32_t nid;
    if (net->free_head > 0) {
        /* reuse a dead slot (stable ID — keeps worklist valid) */
        nid = net->free_list[--net->free_head];
    } else {
        if (net->count >= net->cap) {
            uint32_t new_cap = net->cap * 2;
            if (new_cap > NET_MAX_NODES) new_cap = NET_MAX_NODES;
            if (net->count >= new_cap) {
                fprintf(stderr, "net: node cap (%u) reached (live=%u free=%u)\n",
                        NET_MAX_NODES, net->live, net->free_head);
                exit(1);
            }
            net->nodes = (Node*)realloc(net->nodes, new_cap * sizeof(Node));
            assert(net->nodes);
            memset(net->nodes + net->cap, 0, (new_cap - net->cap) * sizeof(Node));
            net->cap = new_cap;
        }
        nid = net->count++;
    }
    net->live++;
    Node *n = &net->nodes[nid];
    n->tag   = tag;
    n->arity = arity;
    n->meta  = meta;
    n->alive = 1;
    for (int i = 0; i < MAX_PORTS; i++) n->ports[i] = NULL_PORT;
    return nid;
}

static void net_kill(Net *net, uint32_t nid) {
    /* Mark node dead and push onto free-list for reuse. */
    if (!net->nodes[nid].alive) return;
    net->nodes[nid].alive = 0;
    if (net->live > 0) net->live--;
    if (net->free_head >= net->free_cap) {
        net->free_cap *= 2;
        net->free_list = (uint32_t*)realloc(net->free_list,
                                             net->free_cap * sizeof(uint32_t));
        assert(net->free_list);
    }
    net->free_list[net->free_head++] = nid;
}

/* Connect port (a, pa) ↔ (b, pb) bidirectionally. */
/* Get node id from a packed port value. */
static inline uint32_t port_node(uint32_t p) { return p / MAX_PORTS; }
/* Get port index from a packed port value. */
static inline int      port_idx (uint32_t p) { return (int)(p % MAX_PORTS); }

static void net_free(Net *net) { free(net->nodes); free(net->free_list); }

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

/* ── Global function table (v0.11 FREF support) ──────────────────────────── */
#define MAX_FNS 512

typedef struct {
    uint32_t  count;
    Node     *nodes;   /* packed port values (local nids 0..count-1) */
    uint32_t  root;
} FnTemplate;

/* Function template table (FREF support).
 * 
 * Global state necessary for recursive function calls: NELA-C uses lazy
 * function instantiation via FREF nodes, which deep-copy function templates
 * on demand. The template storage must be global to be accessible at net
 * reduction time.
 * 
 * Semantically: templates are immutable after load_nelac(), making this
 * race-free for sequential execution. Parallelization would require atomic
 * reference counting, but doesn't affect net reduction semantics.
 * 
 * This represents a pragmatic compilation strategy (late binding) rather
 * than a semantic requirement of interaction nets.
 */
static FnTemplate g_fns[MAX_FNS];
static int        g_fn_count = 0;

static void reconstruct_ports_local(Node *nodes, uint32_t count) {
    for (uint32_t a = 0; a < count; a++) {
        if (!nodes[a].alive) continue;
        for (int pa = 0; pa <= nodes[a].arity; pa++) {
            uint32_t b = nodes[a].ports[pa];
            if (b == NULL_PORT) continue;
            if (b >= count) { nodes[a].ports[pa] = b * MAX_PORTS + 0; continue; }
            int pb = -1;
            for (int q = 0; q <= nodes[b].arity; q++) {
                if (nodes[b].ports[q] == a) { pb = q; break; }
            }
            nodes[a].ports[pa] = (pb >= 0) ? b * MAX_PORTS + pb : b * MAX_PORTS + 0;
        }
    }
}

static uint32_t instantiate_fn(Net *net, int fn_id) {
    FnTemplate *t = &g_fns[fn_id];
    uint32_t *nid_map = (uint32_t*)malloc(t->count * sizeof(uint32_t));
    for (uint32_t i = 0; i < t->count; i++)
        nid_map[i] = net_alloc(net, t->nodes[i].tag, t->nodes[i].arity, t->nodes[i].meta);
    for (uint32_t i = 0; i < t->count; i++) {
        Node *dst = &net->nodes[nid_map[i]];
        for (int p = 0; p <= t->nodes[i].arity; p++) {
            uint32_t packed = t->nodes[i].ports[p];
            if (packed == NULL_PORT)
                dst->ports[p] = NULL_PORT;
            else
                dst->ports[p] = nid_map[port_node(packed)] * MAX_PORTS + port_idx(packed);
        }
    }
    uint32_t result = nid_map[t->root];
    free(nid_map);
    return result;
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
    uint8_t version = buf[5];
    uint32_t node_count = read_u32(buf + 6);

    net_init(net, node_count + 64);

    size_t off = 10;
    for (uint32_t i = 0; i < node_count; i++) {
        uint8_t  tag   = buf[off++];
        uint8_t  arity = buf[off++];
        int64_t  meta  = read_i64(buf + off); off += 8;
        uint32_t nid   = net_alloc(net, tag, arity, meta);
        for (int p = 0; p <= arity; p++) {
            uint32_t raw = read_u32(buf + off); off += 4;
            net->nodes[nid].ports[p] = (raw == NULL_PORT) ? NULL_PORT : raw;
        }
    }
    uint32_t root = read_u32(buf + off); off += 4;

    /* Reconstruct bidirectional packed ports for the main net. */
    uint32_t n = net->count;
    for (uint32_t a = 0; a < n; a++) {
        if (!net->nodes[a].alive) continue;
        for (int pa = 0; pa <= net->nodes[a].arity; pa++) {
            uint32_t b = net->nodes[a].ports[pa];
            if (b == NULL_PORT) continue;
            int pb = -1;
            for (int q = 0; q <= net->nodes[b].arity; q++) {
                if (net->nodes[b].ports[q] == a) { pb = q; break; }
            }
            net->nodes[a].ports[pa] = (pb >= 0) ? b * MAX_PORTS + pb : b * MAX_PORTS + 0;
        }
    }

    /* ── Parse function table (version 2) ─────────────────────────────── */
    if (version == 2 && off + 4 <= (size_t)fsize) {
        uint32_t fn_count = read_u32(buf + off); off += 4;
        g_fn_count = (int)(fn_count < MAX_FNS ? fn_count : MAX_FNS);
        for (int fi = 0; fi < g_fn_count; fi++) {
            uint32_t nc = read_u32(buf + off); off += 4;
            g_fns[fi].count = nc;
            g_fns[fi].nodes = (Node*)malloc(nc * sizeof(Node));
            for (uint32_t i = 0; i < nc; i++) {
                Node *nd     = &g_fns[fi].nodes[i];
                nd->tag      = buf[off++];
                nd->arity    = buf[off++];
                nd->meta     = read_i64(buf + off); off += 8;
                nd->alive    = 1;
                for (int p = 0; p <= nd->arity; p++) {
                    uint32_t raw = read_u32(buf + off); off += 4;
                    nd->ports[p] = (raw == NULL_PORT) ? NULL_PORT : raw;
                }
            }
            g_fns[fi].root = read_u32(buf + off); off += 4;
            /* Reconstruct packed ports within this sub-net (local nids). */
            reconstruct_ports_local(g_fns[fi].nodes, nc);
        }
    }

    free(buf);
    return root;
}

/* ── SIC Reducer ─────────────────────────────────────────────────────────── */

/* Erase a subgraph rooted at principal port of node nid. */
static void erase(Net *net, uint32_t nid);

static inline int is_num_atom(uint8_t tag) {
    return tag == TAG_INT || tag == TAG_FLT;
}

static inline int is_cmp_atom(uint8_t tag) {
    return tag == TAG_INT || tag == TAG_FLT || tag == TAG_STR || tag == TAG_BOO;
}

static inline int is_binary_ready_op(uint8_t tag) {
    switch (tag) {
        case TAG_ADD: case TAG_SUB: case TAG_MUL: case TAG_DIV: case TAG_MOD:
        case TAG_EQL: case TAG_LTH: case TAG_LEQ: case TAG_GTH: case TAG_GEQ:
        case TAG_AND: case TAG_ORR:
            return 1;
        default:
            return 0;
    }
}

static inline int is_unary_ready_op(uint8_t tag) {
    return tag == TAG_NEG || tag == TAG_NOT ||
           tag == TAG_SIN || tag == TAG_COS || tag == TAG_SQRT ||
           tag == TAG_FLOOR || tag == TAG_CEIL || tag == TAG_ROUND ||
           tag == TAG_ABSS || tag == TAG_ORD || tag == TAG_CHR;
}

static double meta_to_double(int64_t meta);
static int64_t double_to_meta(double d);

static inline int is_value_atom_tag(uint8_t tag) {
    return tag == TAG_INT || tag == TAG_FLT || tag == TAG_STR ||
           tag == TAG_BOO || tag == TAG_NIL;
}

static uint32_t clone_value_atom(Net *net, const Node *n) {
    if (!is_value_atom_tag(n->tag)) return UINT32_MAX;
    return net_alloc(net, n->tag, 0, n->meta);
}

static int cmp_filter_pred(uint8_t pred_tag, const Node *x, const Node *pivot) {
    if (!x || !pivot) return 0;
    if ((x->tag == TAG_FLT || pivot->tag == TAG_FLT) &&
        (x->tag == TAG_INT || x->tag == TAG_FLT || x->tag == TAG_BOO) &&
        (pivot->tag == TAG_INT || pivot->tag == TAG_FLT || pivot->tag == TAG_BOO)) {
        double xv = (x->tag == TAG_FLT) ? meta_to_double(x->meta) : (double)x->meta;
        double pv = (pivot->tag == TAG_FLT) ? meta_to_double(pivot->meta) : (double)pivot->meta;
        switch (pred_tag) {
            case TAG_FILT_LE: return xv <= pv;
            case TAG_FILT_GT: return xv >  pv;
            case TAG_FILT_LT: return xv <  pv;
            case TAG_FILT_GE: return xv >= pv;
            case TAG_FILT_EQ: return xv == pv;
            default: return 0;
        }
    }
    int64_t xv = x->meta, pv = pivot->meta;
    switch (pred_tag) {
        case TAG_FILT_LE: return xv <= pv;
        case TAG_FILT_GT: return xv >  pv;
        case TAG_FILT_LT: return xv <  pv;
        case TAG_FILT_GE: return xv >= pv;
        case TAG_FILT_EQ: return xv == pv;
        default: return 0;
    }
}

static inline int is_cont_producer_tag(uint8_t tag) {
    switch (tag) {
        case TAG_APP:
        case TAG_IFT:
        case TAG_ADD: case TAG_SUB: case TAG_MUL: case TAG_DIV: case TAG_MOD:
        case TAG_NEG:
        case TAG_EQL: case TAG_LTH: case TAG_LEQ: case TAG_GTH: case TAG_GEQ:
        case TAG_AND: case TAG_ORR: case TAG_NOT:
        case TAG_IOKEY: case TAG_IOPRT:
        case TAG_MAT:
        case TAG_FST: case TAG_SND:
        case TAG_HED: case TAG_TAL: case TAG_GET: case TAG_LEN: case TAG_ARR: case TAG_AST:
        case TAG_SIN: case TAG_COS: case TAG_SQRT: case TAG_FLOOR:
        case TAG_CEIL: case TAG_ROUND: case TAG_ABSS: case TAG_ORD: case TAG_CHR:
        case TAG_APPEND: case TAG_FILT_LE: case TAG_FILT_GT: case TAG_FILT_LT:
        case TAG_FILT_GE: case TAG_FILT_EQ: case TAG_TAKE: case TAG_DROP:
            return 1;
        default:
            return 0;
    }
}

static int resolve_atom_through_var(Net *net, uint32_t packed, uint32_t *atom_nid) {
    if (packed == NULL_PORT) return 0;
    uint32_t cur = port_node(packed);
    /* guard against malformed cycles */
    for (uint32_t hops = 0; hops < net->count + 1; hops++) {
        if (cur >= net->count) return 0;
        Node *n = &net->nodes[cur];
        if (!n->alive) return 0;
        if (n->tag != TAG_VAR) {
            *atom_nid = cur;
            return 1;
        }
        uint32_t peer = n->ports[1];
        if (peer == NULL_PORT) return 0;
        cur = port_node(peer);
    }
    return 0;
}

static int resolve_node_through_var(Net *net, uint32_t packed, uint32_t *nid_out) {
    if (packed == NULL_PORT) return 0;
    uint32_t cur = port_node(packed);
    for (uint32_t hops = 0; hops < net->count + 1; hops++) {
        if (cur >= net->count) return 0;
        Node *n = &net->nodes[cur];
        if (!n->alive) return 0;
        if (n->tag != TAG_VAR) {
            *nid_out = cur;
            return 1;
        }
        uint32_t peer = n->ports[1];
        if (peer == NULL_PORT) return 0;
        cur = port_node(peer);
    }
    return 0;
}

static void net_link(Net *net, uint32_t pa_packed, uint32_t pb_packed) {
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

    /* Capture aux-neighbors, then kill first to break cycles during recursion. */
    uint32_t peers[MAX_PORTS];
    int np = 0;
    for (int i = 1; i <= n->arity && i < MAX_PORTS; i++) {
        uint32_t p = n->ports[i];
        if (p != NULL_PORT) peers[np++] = port_node(p);
    }

    net_kill(net, nid);

    for (int k = 0; k < np; k++) {
        uint32_t peer = peers[k];
        if (peer < net->count && net->nodes[peer].alive)
            erase(net, peer);
    }
}

/* Fire one interaction rule for the active pair (a ⊳ b).
 * Returns 1 if a rule fired, 0 otherwise. */
static int fire(Net *net, uint32_t a, uint32_t b);
static int scan_fire_ready_ops(Net *net);
static double meta_to_double(int64_t meta);
static int64_t double_to_meta(double d);

/* ── I/O callbacks ────────────────────────────────────────────────────────── */

static int g_io_enabled = 0;  /* set to 1 by main when running interactively */
static struct termios g_old_tio;

static void io_raw_on(void) {
    struct termios t;
    tcgetattr(STDIN_FILENO, &g_old_tio);
    t = g_old_tio;
    t.c_lflag &= ~(ICANON | ECHO);
    t.c_cc[VMIN]  = 1;
    t.c_cc[VTIME] = 0;
    tcsetattr(STDIN_FILENO, TCSANOW, &t);
}
static void io_raw_off(void) {
    tcsetattr(STDIN_FILENO, TCSANOW, &g_old_tio);
}

/* shade integer → block char pair */
static const char *g_shade[] = {"  ", "\xc2\xb7\xc2\xb7", "\xe2\x96\x92\xe2\x96\x92",
                                  "\xe2\x96\x93\xe2\x96\x93", "\xe2\x96\x88\xe2\x96\x88"};

static char io_getch(void) {
    char c = 0;
    ssize_t n = read(STDIN_FILENO, &c, 1);
    if (n <= 0) return 'q';  /* EOF or error → quit */
    /* map arrow keys: ESC [ A/B/C/D → w/s/d/a */
    if (c == 27) {
        char seq[2] = {0,0};
        if (read(STDIN_FILENO, &seq[0], 1) > 0 && seq[0] == '[') {
            if (read(STDIN_FILENO, &seq[1], 1) > 0) {
                switch(seq[1]) {
                    case 'A': c = 'w'; break;
                    case 'B': c = 's'; break;
                    case 'C': c = 'd'; break;
                    case 'D': c = 'a'; break;
                }
            }
        }
    }
    return c;
}

/* print_frame: frame is a CON-chain of rows, each row a CON-chain of shade ints */
static void io_print_frame(Net *net, uint32_t frame_nid) {
    /* move cursor to top-left */
    printf("\033[H");
    uint32_t row_cur = frame_nid;
    while (net->nodes[row_cur].tag == TAG_CON) {
        uint32_t row_node = port_node(net->nodes[row_cur].ports[1]);
        /* print each column in this row */
        uint32_t col_cur = row_node;
        while (net->nodes[col_cur].tag == TAG_CON) {
            uint32_t shade_nid = port_node(net->nodes[col_cur].ports[1]);
            int shade = (int)net->nodes[shade_nid].meta;
            if (shade < 0) shade = 0;
            if (shade > 4) shade = 4;
            printf("%s", g_shade[shade]);
            uint32_t tp = net->nodes[col_cur].ports[2];
            if (tp == NULL_PORT) break;
            col_cur = port_node(tp);
        }
        printf("\r\n");
        uint32_t tp = net->nodes[row_cur].ports[2];
        if (tp == NULL_PORT) break;
        row_cur = port_node(tp);
    }
    fflush(stdout);
}

/* ── Worklist queue for SIC reducer ──────────────────────────────────────── */

typedef struct {
    uint32_t *data;
    uint32_t  head, tail, cap;
} WQ;

static WQ g_wq;

static void wq_init(uint32_t cap) {
    g_wq.data = (uint32_t*)malloc(cap * sizeof(uint32_t));
    assert(g_wq.data);
    g_wq.head = g_wq.tail = 0;
    g_wq.cap  = cap;
}
static void wq_free(void) { free(g_wq.data); g_wq.data = NULL; }

static void wq_push(uint32_t nid) {
    uint32_t next = (g_wq.tail + 1) % g_wq.cap;
    if (next == g_wq.head) {
        /* grow */
        uint32_t new_cap = g_wq.cap * 2;
        uint32_t *nd = (uint32_t*)malloc(new_cap * sizeof(uint32_t));
        assert(nd);
        uint32_t i = 0;
        uint32_t r = g_wq.head;
        while (r != g_wq.tail) { nd[i++] = g_wq.data[r]; r = (r+1) % g_wq.cap; }
        free(g_wq.data);
        g_wq.data = nd; g_wq.head = 0; g_wq.tail = i; g_wq.cap = new_cap;
        next = (g_wq.tail + 1) % g_wq.cap;
    }
    g_wq.data[g_wq.tail] = nid;
    g_wq.tail = (g_wq.tail + 1) % g_wq.cap;
}

static int wq_empty(void) { return g_wq.head == g_wq.tail; }

static uint32_t wq_pop(void) {
    uint32_t v = g_wq.data[g_wq.head];
    g_wq.head = (g_wq.head + 1) % g_wq.cap;
    return v;
}

/* Schedule nid if it forms an active pair with its principal-port peer. */
static void wq_try_push(Net *net, uint32_t nid) {
    if (nid >= net->count || !net->nodes[nid].alive) return;
    uint32_t pp = net->nodes[nid].ports[0];
    if (pp == NULL_PORT) return;
    uint32_t j = port_node(pp);
    if (!net->nodes[j].alive) return;
    if (net->nodes[nid].tag == TAG_VAR) {
        /* VAR can forward from whichever peer port it is attached to. */
        wq_push(nid);
        return;
    }
    if (port_idx(pp) != 0) return;
    /* Push the smaller nid to avoid duplicate scheduling of the same pair. */
    if (nid < j) wq_push(nid);
}

static int collect_frontier(Net *net, uint32_t nid, uint32_t *out, int out_cap, int at) {
    if (nid >= net->count || !net->nodes[nid].alive) return at;
    Node *n = &net->nodes[nid];
    for (int p = 0; p <= n->arity && at < out_cap; p++) {
        uint32_t pv = n->ports[p];
        if (pv == NULL_PORT) continue;
        out[at++] = port_node(pv);
    }
    return at;
}

#define REDUCE_MAX_STEPS  200000000u  /* 200M steps hard limit */

static void reduce(Net *net, uint32_t root) {
    /* Seed from root only (demand-driven), not from all initial active pairs. */
    wq_init(4096);
    wq_try_push(net, root);
    if (root < net->count) {
        Node *r = &net->nodes[root];
        uint32_t seeds[2];
        int nseeds = 0;

        if (r->ports[0] != NULL_PORT) seeds[nseeds++] = port_node(r->ports[0]);
        /* Compiled entry roots are often VAR nodes with the live edge on aux port 1. */
        if (r->tag == TAG_VAR && r->ports[1] != NULL_PORT)
            seeds[nseeds++] = port_node(r->ports[1]);

        for (int s = 0; s < nseeds; s++) {
            uint32_t nid = seeds[s];
            wq_try_push(net, nid);
            if (nid < net->count && net->nodes[nid].alive) {
                uint32_t pp = net->nodes[nid].ports[0];
                if (pp != NULL_PORT) {
                    uint32_t p1 = port_node(pp);
                    wq_try_push(net, p1);
                    if (p1 < net->count && net->nodes[p1].alive) {
                        uint32_t pp2 = net->nodes[p1].ports[0];
                        if (pp2 != NULL_PORT) wq_try_push(net, port_node(pp2));
                    }
                }
            }
        }
    }

    uint32_t steps = 0;
    uint32_t hwm   = net->count;   /* high-water-mark: next unscanned nid */
    while (!wq_empty()) {
        if (steps >= REDUCE_MAX_STEPS) {
            /* Step limit reached: hard termination (should be unreachable in well-behaved nets). */
            wq_free(); exit(1);
        }
        uint32_t i = wq_pop();
        if (!net->nodes[i].alive) continue;
        uint32_t pp = net->nodes[i].ports[0];
        if (pp == NULL_PORT) continue;
        uint32_t j = port_node(pp);
        if (!net->nodes[j].alive) continue;
        if (net->nodes[i].tag != TAG_VAR && port_idx(pp) != 0) continue;

        uint32_t frontier[2 * MAX_PORTS * 2];
        int fn = 0;
        fn = collect_frontier(net, i, frontier, (int)(sizeof(frontier) / sizeof(frontier[0])), fn);
        fn = collect_frontier(net, j, frontier, (int)(sizeof(frontier) / sizeof(frontier[0])), fn);

        if (fire(net, i, j)) {
            steps++;
            /* Diagnostic: step counter now removed to preserve side-effect-free guarantee.
             * (Commented out: fprintf(stderr, "step %%uM..."); )
             * Uncomment for debugging node explosion issues. */
            wq_try_push(net, i);
            wq_try_push(net, j);
            for (int t = 0; t < fn; t++) {
                uint32_t nid = frontier[t];
                wq_try_push(net, nid);
                if (nid < net->count && net->nodes[nid].alive) {
                    uint32_t p2 = net->nodes[nid].ports[0];
                    if (p2 != NULL_PORT) wq_try_push(net, port_node(p2));
                }
            }
            /* scan any newly allocated nodes */
            for (uint32_t k = hwm; k < net->count; k++)
                wq_try_push(net, k);
            hwm = net->count;
        }
    }

    /* Some primitive op nodes become ready without ever forming a principal
     * pair. Run fixpoint scans so those nodes can still reduce. */
    while (scan_fire_ready_ops(net) > 0) {
        while (!wq_empty()) {
            if (steps >= REDUCE_MAX_STEPS) {
                /* Step limit reached: hard termination (should be unreachable in well-behaved nets). */
                wq_free(); exit(1);
            }
            uint32_t i = wq_pop();
            if (!net->nodes[i].alive) continue;
            uint32_t pp = net->nodes[i].ports[0];
            if (pp == NULL_PORT) continue;
            uint32_t j = port_node(pp);
            if (!net->nodes[j].alive) continue;
            if (net->nodes[i].tag != TAG_VAR && port_idx(pp) != 0) continue;

            uint32_t frontier[2 * MAX_PORTS * 2];
            int fn = 0;
            fn = collect_frontier(net, i, frontier, (int)(sizeof(frontier) / sizeof(frontier[0])), fn);
            fn = collect_frontier(net, j, frontier, (int)(sizeof(frontier) / sizeof(frontier[0])), fn);

            if (fire(net, i, j)) {
                steps++;
                /* Diagnostic: step counter now removed to preserve side-effect-free guarantee.
                 * (Commented out: fprintf(stderr, "step %%uM..."); )
                 * Uncomment for debugging node explosion issues. */
                wq_try_push(net, i);
                wq_try_push(net, j);
                for (int t = 0; t < fn; t++) {
                    uint32_t nid = frontier[t];
                    wq_try_push(net, nid);
                    if (nid < net->count && net->nodes[nid].alive) {
                        uint32_t p2 = net->nodes[nid].ports[0];
                        if (p2 != NULL_PORT) wq_try_push(net, port_node(p2));
                    }
                }
                for (uint32_t k = hwm; k < net->count; k++)
                    wq_try_push(net, k);
                hwm = net->count;
            }
        }
    }
    wq_free();
}

static int scan_fire_ready_ops(Net *net) {
    int fired = 0;
    uint32_t n0 = net->count;
    for (uint32_t i = 0; i < n0; i++) {
        if (!net->nodes[i].alive) continue;
        Node *op = &net->nodes[i];

        if (is_binary_ready_op(op->tag)) {
            uint32_t pp = op->ports[0];
            if (pp == NULL_PORT || port_idx(pp) != 0) continue;
            uint32_t lp = op->ports[1], rp = op->ports[2], resp = op->ports[3];
            if (lp == NULL_PORT || rp == NULL_PORT || resp == NULL_PORT) continue;
            uint32_t ln = 0, rn = 0;
            if (!resolve_atom_through_var(net, lp, &ln)) continue;
            if (!resolve_atom_through_var(net, rp, &rn)) continue;
            if (ln >= net->count || rn >= net->count) continue;
            if (!net->nodes[ln].alive || !net->nodes[rn].alive) continue;
            Node *lnode = &net->nodes[ln];
            Node *rnode = &net->nodes[rn];

            if (op->tag == TAG_AND || op->tag == TAG_ORR) {
                if (lnode->tag != TAG_BOO || rnode->tag != TAG_BOO) continue;
                int res = (op->tag == TAG_AND)
                    ? (int)(lnode->meta && rnode->meta)
                    : (int)(lnode->meta || rnode->meta);
                erase(net, port_node(lp));
                erase(net, port_node(rp));
                net_kill(net, i);
                uint32_t r = net_alloc(net, TAG_BOO, 0, res);
                net_link(net, r * MAX_PORTS + 0, resp);
                wq_try_push(net, r);
                fired++;
                continue;
            }

            if (op->tag >= TAG_ADD && op->tag <= TAG_MOD) {
                if (!is_num_atom(lnode->tag) || !is_num_atom(rnode->tag)) continue;
                int is_flt = (lnode->tag == TAG_FLT || rnode->tag == TAG_FLT);
                uint32_t res_nid;
                if (is_flt) {
                    double lv = (lnode->tag==TAG_FLT) ? meta_to_double(lnode->meta) : (double)lnode->meta;
                    double rv = (rnode->tag==TAG_FLT) ? meta_to_double(rnode->meta) : (double)rnode->meta;
                    double out = 0.0;
                    if (op->tag == TAG_ADD) out = lv + rv;
                    else if (op->tag == TAG_SUB) out = lv - rv;
                    else if (op->tag == TAG_MUL) out = lv * rv;
                    else if (op->tag == TAG_DIV) out = (rv != 0.0 ? lv / rv : 0.0);
                    else out = fmod(lv, rv != 0.0 ? rv : 1.0);
                    res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(out));
                } else {
                    int64_t lv = lnode->meta, rv = rnode->meta;
                    int64_t out = 0;
                    if (op->tag == TAG_ADD) out = lv + rv;
                    else if (op->tag == TAG_SUB) out = lv - rv;
                    else if (op->tag == TAG_MUL) out = lv * rv;
                    else if (op->tag == TAG_DIV) out = (rv != 0 ? lv / rv : 0);
                    else out = (rv != 0 ? lv % rv : 0);
                    res_nid = net_alloc(net, TAG_INT, 0, out);
                }
                erase(net, port_node(lp));
                erase(net, port_node(rp));
                net_kill(net, i);
                net_link(net, res_nid * MAX_PORTS + 0, resp);
                wq_try_push(net, res_nid);
                fired++;
                continue;
            }

            if (op->tag >= TAG_EQL && op->tag <= TAG_GEQ) {
                if (!is_cmp_atom(lnode->tag) || !is_cmp_atom(rnode->tag)) continue;
                int result;
                if (lnode->tag == TAG_FLT || rnode->tag == TAG_FLT) {
                    double lv = (lnode->tag==TAG_FLT) ? meta_to_double(lnode->meta) : (double)lnode->meta;
                    double rv = (rnode->tag==TAG_FLT) ? meta_to_double(rnode->meta) : (double)rnode->meta;
                    if (op->tag == TAG_EQL) result = (lv == rv);
                    else if (op->tag == TAG_LTH) result = (lv < rv);
                    else if (op->tag == TAG_LEQ) result = (lv <= rv);
                    else if (op->tag == TAG_GTH) result = (lv > rv);
                    else result = (lv >= rv);
                } else {
                    int64_t lv = lnode->meta, rv = rnode->meta;
                    if (op->tag == TAG_EQL) result = (lv == rv);
                    else if (op->tag == TAG_LTH) result = (lv < rv);
                    else if (op->tag == TAG_LEQ) result = (lv <= rv);
                    else if (op->tag == TAG_GTH) result = (lv > rv);
                    else result = (lv >= rv);
                }
                erase(net, port_node(lp));
                erase(net, port_node(rp));
                net_kill(net, i);
                uint32_t res_nid = net_alloc(net, TAG_BOO, 0, (int64_t)result);
                net_link(net, res_nid * MAX_PORTS + 0, resp);
                wq_try_push(net, res_nid);
                fired++;
                continue;
            }
        }

        if (is_unary_ready_op(op->tag)) {
            uint32_t pp = op->ports[0];
            if (pp == NULL_PORT || port_idx(pp) != 0) continue;
            uint32_t vp = op->ports[1], resp = op->ports[2];
            if (vp == NULL_PORT || resp == NULL_PORT) continue;
            uint32_t vn = 0;
            if (!resolve_atom_through_var(net, vp, &vn)) continue;
            if (vn >= net->count || !net->nodes[vn].alive) continue;
            Node *vnode = &net->nodes[vn];

            if (op->tag == TAG_NEG) {
                if (!is_num_atom(vnode->tag)) continue;
                uint32_t res_nid;
                if (vnode->tag == TAG_FLT)
                    res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(-meta_to_double(vnode->meta)));
                else
                    res_nid = net_alloc(net, TAG_INT, 0, -vnode->meta);
                erase(net, port_node(vp));
                net_kill(net, i);
                net_link(net, res_nid * MAX_PORTS + 0, resp);
                wq_try_push(net, res_nid);
                fired++;
                continue;
            }

            if (op->tag == TAG_NOT) {
                if (vnode->tag != TAG_BOO) continue;
                uint32_t res_nid = net_alloc(net, TAG_BOO, 0, (int64_t)(!(int)vnode->meta));
                erase(net, port_node(vp));
                net_kill(net, i);
                net_link(net, res_nid * MAX_PORTS + 0, resp);
                wq_try_push(net, res_nid);
                fired++;
                continue;
            }

            if (op->tag == TAG_SIN || op->tag == TAG_COS || op->tag == TAG_SQRT ||
                op->tag == TAG_FLOOR || op->tag == TAG_CEIL || op->tag == TAG_ROUND ||
                op->tag == TAG_ABSS) {
                if (!is_num_atom(vnode->tag)) continue;
                double x = (vnode->tag == TAG_FLT) ? meta_to_double(vnode->meta) : (double)vnode->meta;
                uint32_t res_nid = 0;
                if (op->tag == TAG_SIN) {
                    res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(sin(x)));
                } else if (op->tag == TAG_COS) {
                    res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(cos(x)));
                } else if (op->tag == TAG_SQRT) {
                    double y = (x >= 0.0) ? sqrt(x) : 0.0;
                    res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(y));
                } else if (op->tag == TAG_FLOOR) {
                    res_nid = net_alloc(net, TAG_INT, 0, (int64_t)floor(x));
                } else if (op->tag == TAG_CEIL) {
                    res_nid = net_alloc(net, TAG_INT, 0, (int64_t)ceil(x));
                } else if (op->tag == TAG_ROUND) {
                    res_nid = net_alloc(net, TAG_INT, 0, (int64_t)llround(x));
                } else { /* TAG_ABSS */
                    if (vnode->tag == TAG_FLT)
                        res_nid = net_alloc(net, TAG_FLT, 0, double_to_meta(fabs(x)));
                    else
                        res_nid = net_alloc(net, TAG_INT, 0, llabs(vnode->meta));
                }
                erase(net, port_node(vp));
                net_kill(net, i);
                net_link(net, res_nid * MAX_PORTS + 0, resp);
                wq_try_push(net, res_nid);
                fired++;
                continue;
            }

            if (op->tag == TAG_ORD) {
                if (vnode->tag != TAG_STR) continue;
                uint32_t res_nid = net_alloc(net, TAG_INT, 0, vnode->meta);
                erase(net, port_node(vp));
                net_kill(net, i);
                net_link(net, res_nid * MAX_PORTS + 0, resp);
                wq_try_push(net, res_nid);
                fired++;
                continue;
            }

            if (op->tag == TAG_CHR) {
                if (vnode->tag != TAG_INT) continue;
                uint32_t res_nid = net_alloc(net, TAG_STR, 0, vnode->meta & 0xFF);
                erase(net, port_node(vp));
                net_kill(net, i);
                net_link(net, res_nid * MAX_PORTS + 0, resp);
                wq_try_push(net, res_nid);
                fired++;
                continue;
            }
        }
    }

    /* Any new active pairs created during scan should run in normal loop. */
    for (uint32_t k = 0; k < net->count; k++)
        wq_try_push(net, k);
    return fired;
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
        net_kill(net, app);
        net_kill(net, lam);
        net_link(net, result, body);
        net_link(net, arg,    var);
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
        net_kill(net, dup);
        net_kill(net, lam);
        /* create lam_a, lam_b */
        uint32_t la = net_alloc(net, TAG_LAM, 2, 0);
        uint32_t lb = net_alloc(net, TAG_LAM, 2, 0);
        /* dup for body, dup for var */
        uint32_t db = net_alloc(net, TAG_DUP, 2, 0);
        uint32_t dv = net_alloc(net, TAG_DUP, 2, 0);
        /* wire dup_body ⊳ body, dup_var ⊳ var */
        net_link(net, db * MAX_PORTS + 0, body);
        net_link(net, dv * MAX_PORTS + 0, var);
        /* lam_a body ← db copy1, var ← dv copy1 */
        net_link(net, la * MAX_PORTS + 1, db * MAX_PORTS + 1);
        net_link(net, la * MAX_PORTS + 2, dv * MAX_PORTS + 1);
        /* lam_b body ← db copy2, var ← dv copy2 */
        net_link(net, lb * MAX_PORTS + 1, db * MAX_PORTS + 2);
        net_link(net, lb * MAX_PORTS + 2, dv * MAX_PORTS + 2);
        /* connect to dup's output ports */
        net_link(net, la * MAX_PORTS + 0, ca);
        net_link(net, lb * MAX_PORTS + 0, cb);
        return 1;
    }

    /* ── DUP ⊳ atom/data  (copy non-lambda values) ─────────────────────── */
    if (a->tag == TAG_DUP || b->tag == TAG_DUP) {
        uint32_t dup = (a->tag == TAG_DUP) ? ai : bi;
        uint32_t val = (a->tag == TAG_DUP) ? bi : ai;
        uint32_t ca = net->nodes[dup].ports[1];
        uint32_t cb = net->nodes[dup].ports[2];
        Node *vn = &net->nodes[val];

        if (vn->tag == TAG_INT || vn->tag == TAG_FLT || vn->tag == TAG_STR ||
            vn->tag == TAG_BOO || vn->tag == TAG_NIL || vn->tag == TAG_IOT ||
            vn->tag == TAG_FREF) {
            uint32_t va = net_alloc(net, vn->tag, 0, vn->meta);
            uint32_t vb = net_alloc(net, vn->tag, 0, vn->meta);
            net_kill(net, dup);
            net_kill(net, val);
            net_link(net, va * MAX_PORTS + 0, ca);
            net_link(net, vb * MAX_PORTS + 0, cb);
            return 1;
        }

        if (vn->tag == TAG_CON || vn->tag == TAG_PAR) {
            uint8_t t = vn->tag;
            uint32_t hp = vn->ports[1];
            uint32_t tp = vn->ports[2];
            net_kill(net, dup);
            net_kill(net, val);

            uint32_t a1 = net_alloc(net, t, 2, 0);
            uint32_t b1 = net_alloc(net, t, 2, 0);
            uint32_t dh = net_alloc(net, TAG_DUP, 2, 0);
            uint32_t dt = net_alloc(net, TAG_DUP, 2, 0);

            net_link(net, dh * MAX_PORTS + 0, hp);
            net_link(net, dt * MAX_PORTS + 0, tp);

            net_link(net, a1 * MAX_PORTS + 1, dh * MAX_PORTS + 1);
            net_link(net, b1 * MAX_PORTS + 1, dh * MAX_PORTS + 2);
            net_link(net, a1 * MAX_PORTS + 2, dt * MAX_PORTS + 1);
            net_link(net, b1 * MAX_PORTS + 2, dt * MAX_PORTS + 2);

            net_link(net, a1 * MAX_PORTS + 0, ca);
            net_link(net, b1 * MAX_PORTS + 0, cb);
            return 1;
        }
    }

    /* ── ERA ⊳ anything ─────────────────────────────────────────────────── */
    if (a->tag == TAG_ERA || b->tag == TAG_ERA) {
        uint32_t era   = (a->tag == TAG_ERA) ? ai : bi;
        uint32_t other = (a->tag == TAG_ERA) ? bi : ai;
        net_kill(net, era);
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
        net_kill(net, ift);
        net_kill(net, boo);
        if (cond) {
            net_link(net, then_p, result);
            if (else_p != NULL_PORT) erase(net, port_node(else_p));
        } else {
            net_link(net, else_p, result);
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
            net_kill(net, ln); net_kill(net, rn); net_kill(net, (a->tag == (OPTAG)) ? ai : bi); \
            if (resp != NULL_PORT) net_link(net, res_nid * MAX_PORTS + 0, resp); \
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
            net_kill(net, port_node(vp)); net_kill(net, (a->tag == TAG_NEG) ? ai : bi);
            if (resp != NULL_PORT) net_link(net, res_nid * MAX_PORTS + 0, resp);
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
            net_kill(net, port_node(lp)); net_kill(net, port_node(rp)); net_kill(net, (a->tag == (OPTAG)) ? ai : bi); \
            uint32_t res_nid = net_alloc(net, TAG_BOO, 0, (int64_t)result); \
            if (resp != NULL_PORT) net_link(net, res_nid * MAX_PORTS + 0, resp); \
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
            net_kill(net, port_node(lp)); net_kill(net, port_node(rp)); net_kill(net, (a->tag == TAG_AND) ? ai : bi);
            uint32_t r = net_alloc(net, TAG_BOO, 0, res);
            if (resp != NULL_PORT) net_link(net, r * MAX_PORTS + 0, resp);
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
            net_kill(net, port_node(lp)); net_kill(net, port_node(rp)); net_kill(net, (a->tag == TAG_ORR) ? ai : bi);
            uint32_t r = net_alloc(net, TAG_BOO, 0, res);
            if (resp != NULL_PORT) net_link(net, r * MAX_PORTS + 0, resp);
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
            net_kill(net, port_node(vp)); net_kill(net, (a->tag == TAG_NOT) ? ai : bi);
            uint32_t r = net_alloc(net, TAG_BOO, 0, res);
            if (resp != NULL_PORT) net_link(net, r * MAX_PORTS + 0, resp);
            return 1;
        }
        return 0;
    }

    /* ── FREF ⊳ X  (instantiate function body on demand) ─────────────────── */
    if (a->tag == TAG_FREF || b->tag == TAG_FREF) {
        uint32_t fref  = (a->tag == TAG_FREF) ? ai : bi;
        uint32_t other = (a->tag == TAG_FREF) ? bi : ai;
        int fn_id = (int)(net->nodes[fref].meta);
        net_kill(net, fref);
        /* Validated at bytecode load time; should never fail here. */
        assert(fn_id >= 0 && fn_id < g_fn_count && g_fns[fn_id].nodes != NULL);
        if (fn_id < 0 || fn_id >= g_fn_count || g_fns[fn_id].nodes == NULL) {
            return 0;  /* Fallback: should not reach (assert above) */
        }
        uint32_t new_root = instantiate_fn(net, fn_id);
        /* form active pair: other.principal ↔ new_root.principal */
        net->nodes[other].ports[0]    = new_root * MAX_PORTS + 0;
        net->nodes[new_root].ports[0] = other    * MAX_PORTS + 0;
        return 1;
    }

    /* ── VAR wire: forward through ──────────────────────────────────────── */
    /* VAR is a transparent wire: if one side connects to it, pass through */
    if (a->tag == TAG_VAR || b->tag == TAG_VAR) {
        uint32_t var_nid   = (a->tag == TAG_VAR) ? ai : bi;
        uint32_t other_nid = (a->tag == TAG_VAR) ? bi : ai;
        /* VAR.ports[0] connects to other at some port index.
         * VAR.ports[1] is the other end of the wire.
         * We redirect: disconnect var, link other's connected port to var's peer. */
        uint32_t link = net->nodes[var_nid].ports[0];
        int other_port = (link == NULL_PORT) ? 0 : port_idx(link);
        uint32_t peer = net->nodes[var_nid].ports[1];
        net_kill(net, var_nid);
        if (peer != NULL_PORT) {
            uint32_t prod_nid = port_node(peer);
            int prod_port = port_idx(peer);
            if (other_port == 0 && prod_nid < net->count && net->nodes[prod_nid].alive &&
                prod_port != 0 && is_cont_producer_tag(net->nodes[prod_nid].tag)) {
                /* Continuation wiring: consumer principal requests producer principal,
                 * and producer result port now returns directly to that consumer. */
                net->nodes[other_nid].ports[0] = prod_nid * MAX_PORTS + 0;
                net->nodes[prod_nid].ports[0] = other_nid * MAX_PORTS + 0;
                net->nodes[prod_nid].ports[prod_port] = other_nid * MAX_PORTS + 0;
                return 1;
            }
        }
        /* Connect other's original port to peer */
        if (peer != NULL_PORT) {
            uint32_t peer_nid = port_node(peer);
            int      peer_idx = port_idx(peer);
            net->nodes[other_nid].ports[other_port] = peer;
            net->nodes[peer_nid].ports[peer_idx] = other_nid * MAX_PORTS + other_port;
        }
        return 1;
    }

    /* ── FIX ⊳ LAM  (unroll one step: fix f = f (fix f)) ───────────────── */
    if ((a->tag == TAG_FIX && b->tag == TAG_LAM) ||
        (a->tag == TAG_LAM && b->tag == TAG_FIX)) {
        uint32_t fix_nid = (a->tag == TAG_FIX) ? ai : bi;
        uint32_t lam_nid = (a->tag == TAG_LAM) ? ai : bi;
        /* Create a new FIX node pointing to the same LAM body */
        uint32_t fix2    = net_alloc(net, TAG_FIX, 1, 0);
        uint32_t body    = net->nodes[lam_nid].ports[1];
        uint32_t var     = net->nodes[lam_nid].ports[2];
        uint32_t result  = net->nodes[fix_nid].ports[1]; /* where output goes */
        net_kill(net, fix_nid);
        net_kill(net, lam_nid);
        /* fix2.ports[1] ← result of applying lam to fix2 */
        /* wire: fix2 principal → new APP that applies body to fix2 */
        uint32_t app = net_alloc(net, TAG_APP, 2, 0);
        /* APP.principal ↔ lam_copy: we need a fresh LAM copy */
        uint32_t lam2 = net_alloc(net, TAG_LAM, 2, 0);
        net->nodes[lam2].ports[1] = body;
        if (body != NULL_PORT) {
            uint32_t bn = port_node(body); int bi2 = port_idx(body);
            net->nodes[bn].ports[bi2] = lam2 * MAX_PORTS + 1;
        }
        net->nodes[lam2].ports[2] = var;
        if (var != NULL_PORT) {
            uint32_t vn = port_node(var); int vi = port_idx(var);
            net->nodes[vn].ports[vi] = lam2 * MAX_PORTS + 2;
        }
        /* APP.principal ↔ lam2.principal */
        net->nodes[app].ports[0]  = lam2 * MAX_PORTS + 0;
        net->nodes[lam2].ports[0] = app  * MAX_PORTS + 0;
        /* APP.ports[2] = fix2 (the recursive argument) */
        net->nodes[app].ports[2]  = fix2 * MAX_PORTS + 0;
        net->nodes[fix2].ports[0] = app  * MAX_PORTS + 2;
        /* APP.ports[1] = result */
        net->nodes[app].ports[1] = result;
        if (result != NULL_PORT) {
            uint32_t rn = port_node(result); int ri = port_idx(result);
            net->nodes[rn].ports[ri] = app * MAX_PORTS + 1;
        }
        /* fix2.ports[1] ← lam2 copy (so fix2 can unroll again) */
        uint32_t lam3 = net_alloc(net, TAG_LAM, 2, 0);
        /* shallow copy lam2 body/var — share for now (DUP will handle copying) */
        net->nodes[lam3].ports[1] = body;
        net->nodes[lam3].ports[2] = var;
        net->nodes[fix2].ports[1] = lam3 * MAX_PORTS + 0;
        net->nodes[lam3].ports[0] = fix2 * MAX_PORTS + 1;
        return 1;
    }

    /* ── IOT ⊳ IOKEY  (read a key) ──────────────────────────────────────── */
    if ((a->tag == TAG_IOT && b->tag == TAG_IOKEY) ||
        (a->tag == TAG_IOKEY && b->tag == TAG_IOT)) {
        uint32_t iot_nid   = (a->tag == TAG_IOT)   ? ai : bi;
        uint32_t iokey_nid = (a->tag == TAG_IOKEY) ? ai : bi;
        char     ch        = g_io_enabled ? io_getch() : 'q';
        /* result = PAR(STR(ch), IOT') */
        uint32_t str_nid = net_alloc(net, TAG_STR, 0, (int64_t)(unsigned char)ch);
        uint32_t iot2    = net_alloc(net, TAG_IOT, 0, 0);
        uint32_t par     = net_alloc(net, TAG_PAR, 2, 0);
        net->nodes[par].ports[1] = str_nid * MAX_PORTS + 0;
        net->nodes[str_nid].ports[0] = par * MAX_PORTS + 1;
        net->nodes[par].ports[2] = iot2 * MAX_PORTS + 0;
        net->nodes[iot2].ports[0] = par * MAX_PORTS + 2;
        /* connect par to result port */
        uint32_t result = net->nodes[iokey_nid].ports[1];
        net->nodes[iot_nid].alive   = 0;
        net_kill(net, iokey_nid);
        net_link(net, par * MAX_PORTS + 0, result);
        return 1;
    }

    /* ── IOT ⊳ IOPRT  (print a frame) ───────────────────────────────────── */
    if ((a->tag == TAG_IOT && b->tag == TAG_IOPRT) ||
        (a->tag == TAG_IOPRT && b->tag == TAG_IOT)) {
        uint32_t iot_nid   = (a->tag == TAG_IOT)   ? ai : bi;
        uint32_t ioprt_nid = (a->tag == TAG_IOPRT) ? ai : bi;
        uint32_t frame_port = net->nodes[ioprt_nid].ports[1];
        uint32_t tok_out    = net->nodes[ioprt_nid].ports[3];

        uint32_t frame_nid = 0;
        if (frame_port != NULL_PORT && !resolve_node_through_var(net, frame_port, &frame_nid)) {
            return 0;
        }
        if (frame_port != NULL_PORT) {
            uint8_t t = net->nodes[frame_nid].tag;
            /* Keep IOPRT pending until frame is reduced to a concrete list. */
            if (t != TAG_CON && t != TAG_NIL) return 0;
        }

        if (frame_port != NULL_PORT && g_io_enabled) {
            io_print_frame(net, frame_nid);
        }
        /* return fresh IOT */
        uint32_t iot2 = net_alloc(net, TAG_IOT, 0, 0);
        net->nodes[iot_nid].alive   = 0;
        net_kill(net, ioprt_nid);
        net_link(net, iot2 * MAX_PORTS + 0, tok_out);
        return 1;
    }

    /* ── MAT ⊳ scrutinee  (pattern match) ──────────────────────────────── */
    if (a->tag == TAG_MAT || b->tag == TAG_MAT) {
        uint32_t mat_nid = (a->tag == TAG_MAT) ? ai : bi;
        uint32_t sc_nid  = (a->tag == TAG_MAT) ? bi : ai;
        Node *mat = &net->nodes[mat_nid];
        Node *sc  = &net->nodes[sc_nid];

        int ncases = (int)mat->arity - 1;
        if (ncases <= 0) return 0;
        uint32_t result = mat->ports[mat->arity];

        if (sc->tag == TAG_NIL) {
            uint32_t br = mat->ports[1];
            net_kill(net, mat_nid);
            net_kill(net, sc_nid);
            uint32_t app = net_alloc(net, TAG_APP, 2, 0);
            uint32_t nil_arg = net_alloc(net, TAG_NIL, 0, 0);
            net_link(net, app * MAX_PORTS + 0, br);
            net_link(net, app * MAX_PORTS + 2, nil_arg * MAX_PORTS + 0);
            net_link(net, app * MAX_PORTS + 1, result);
            return 1;
        }

        if (sc->tag == TAG_CON && ncases >= 2) {
            uint32_t br = mat->ports[2];
            uint32_t hp = sc->ports[1];
            uint32_t tp = sc->ports[2];
            net_kill(net, mat_nid);
            net_kill(net, sc_nid);

            uint32_t app1 = net_alloc(net, TAG_APP, 2, 0);
            uint32_t mid  = net_alloc(net, TAG_VAR, 1, 0);
            uint32_t app2 = net_alloc(net, TAG_APP, 2, 0);
            net_link(net, app1 * MAX_PORTS + 0, br);
            net_link(net, app1 * MAX_PORTS + 2, hp);
            net_link(net, app1 * MAX_PORTS + 1, mid * MAX_PORTS + 1);

            net_link(net, app2 * MAX_PORTS + 0, mid * MAX_PORTS + 0);
            net_link(net, app2 * MAX_PORTS + 2, tp);
            net_link(net, app2 * MAX_PORTS + 1, result);
            return 1;
        }

        {
            /* fallback branch: 3rd if present, else 2nd, else 1st */
            int bi_idx = (ncases >= 3) ? 3 : ((ncases >= 2) ? 2 : 1);
            uint32_t br = mat->ports[bi_idx];
            uint32_t scp = sc_nid * MAX_PORTS + 0;
            net_kill(net, mat_nid);

            uint32_t app = net_alloc(net, TAG_APP, 2, 0);
            net_link(net, app * MAX_PORTS + 0, br);
            net_link(net, app * MAX_PORTS + 2, scp);
            net_link(net, app * MAX_PORTS + 1, result);
            return 1;
        }
    }

    /* ── PAR ⊳ FST ───────────────────────────────────────────────────────── */
    if ((a->tag == TAG_PAR && b->tag == TAG_FST) ||
        (a->tag == TAG_FST && b->tag == TAG_PAR)) {
        uint32_t par_nid = (a->tag == TAG_PAR) ? ai : bi;
        uint32_t fst_nid = (a->tag == TAG_FST) ? ai : bi;
        uint32_t left    = net->nodes[par_nid].ports[1];
        uint32_t result  = net->nodes[fst_nid].ports[2];
        uint32_t right   = net->nodes[par_nid].ports[2];
        net_kill(net, par_nid);
        net_kill(net, fst_nid);
        net_link(net, left, result);
        if (right != NULL_PORT) {
            uint32_t era = net_alloc(net, TAG_ERA, 0, 0);
            net_link(net, right, era * MAX_PORTS + 0);
        }
        return 1;
    }

    /* ── PAR ⊳ SND ───────────────────────────────────────────────────────── */
    if ((a->tag == TAG_PAR && b->tag == TAG_SND) ||
        (a->tag == TAG_SND && b->tag == TAG_PAR)) {
        uint32_t par_nid = (a->tag == TAG_PAR) ? ai : bi;
        uint32_t snd_nid = (a->tag == TAG_SND) ? ai : bi;
        uint32_t right   = net->nodes[par_nid].ports[2];
        uint32_t result  = net->nodes[snd_nid].ports[2];
        uint32_t left    = net->nodes[par_nid].ports[1];
        net_kill(net, par_nid);
        net_kill(net, snd_nid);
        net_link(net, right, result);
        if (left != NULL_PORT) {
            uint32_t era = net_alloc(net, TAG_ERA, 0, 0);
            net_link(net, left, era * MAX_PORTS + 0);
        }
        return 1;
    }

    /* ── CON ⊳ HED ───────────────────────────────────────────────────────── */
    if ((a->tag == TAG_CON && b->tag == TAG_HED) ||
        (a->tag == TAG_HED && b->tag == TAG_CON)) {
        uint32_t con_nid = (a->tag == TAG_CON) ? ai : bi;
        uint32_t hed_nid = (a->tag == TAG_HED) ? ai : bi;
        uint32_t head    = net->nodes[con_nid].ports[1];
        uint32_t result  = net->nodes[hed_nid].ports[2];
        uint32_t tail    = net->nodes[con_nid].ports[2];
        net_kill(net, con_nid);
        net_kill(net, hed_nid);
        net_link(net, head, result);
        if (tail != NULL_PORT) {
            uint32_t era = net_alloc(net, TAG_ERA, 0, 0);
            net_link(net, tail, era * MAX_PORTS + 0);
        }
        return 1;
    }

    /* ── CON ⊳ TAL ───────────────────────────────────────────────────────── */
    if ((a->tag == TAG_CON && b->tag == TAG_TAL) ||
        (a->tag == TAG_TAL && b->tag == TAG_CON)) {
        uint32_t con_nid = (a->tag == TAG_CON) ? ai : bi;
        uint32_t tal_nid = (a->tag == TAG_TAL) ? ai : bi;
        uint32_t head    = net->nodes[con_nid].ports[1];
        uint32_t tail    = net->nodes[con_nid].ports[2];
        uint32_t result  = net->nodes[tal_nid].ports[2];
        net_kill(net, con_nid);
        net_kill(net, tal_nid);
        net_link(net, tail, result);
        if (head != NULL_PORT) {
            uint32_t era = net_alloc(net, TAG_ERA, 0, 0);
            net_link(net, head, era * MAX_PORTS + 0);
        }
        return 1;
    }

    /* ── GET: get list idx (binary op-style: fires when list and idx are ready) */
    if (a->tag == TAG_GET || b->tag == TAG_GET) {
        Node *op_n = (a->tag == TAG_GET) ? a : b;
        uint32_t get_nid = (a->tag == TAG_GET) ? ai : bi;
        uint32_t lp  = op_n->ports[1];  /* list */
        uint32_t idxp = op_n->ports[2]; /* index */
        uint32_t resp = op_n->ports[3]; /* result */
        if (lp == NULL_PORT || idxp == NULL_PORT) return 0;
        uint32_t ln = port_node(lp), idxn = port_node(idxp);
        Node *lnode   = &net->nodes[ln];
        Node *idxnode = &net->nodes[idxn];
        if (idxnode->tag != TAG_INT) return 0;
        int64_t idx = idxnode->meta;
        if (lnode->tag == TAG_NIL) {
            /* out of bounds: return 0 */
            net_kill(net, get_nid);
            net_kill(net, idxn);
            net_kill(net, ln);
            if (resp != NULL_PORT) {
                uint32_t zero = net_alloc(net, TAG_INT, 0, 0);
                net_link(net, resp, zero * MAX_PORTS + 0);
            }
            return 1;
        }
        if (lnode->tag != TAG_CON) return 0;
        /* CON: ports[1]=head, ports[2]=tail */
        uint32_t head = lnode->ports[1];
        uint32_t tail = lnode->ports[2];
        net_kill(net, get_nid);
        net_kill(net, idxn);
        net_kill(net, ln);
        if (idx == 0) {
            /* return head, erase tail */
            net_link(net, head, resp);
            if (tail != NULL_PORT) {
                uint32_t era = net_alloc(net, TAG_ERA, 0, 0);
                net_link(net, tail, era * MAX_PORTS + 0);
            }
        } else {
            /* recurse: GET(tail, idx-1) */
            uint32_t new_idx = net_alloc(net, TAG_INT, 0, idx - 1);
            uint32_t new_get = net_alloc(net, TAG_GET, 3, 0);
            net_link(net, new_get * MAX_PORTS + 1, tail);
            net_link(net, new_get * MAX_PORTS + 2, new_idx * MAX_PORTS + 0);
            net_link(net, new_get * MAX_PORTS + 3, resp);
            /* erase head */
            if (head != NULL_PORT) {
                uint32_t era = net_alloc(net, TAG_ERA, 0, 0);
                net_link(net, head, era * MAX_PORTS + 0);
            }
            /* push new_get into worklist via principal pair */
            /* new_get needs a principal connection to fire — hook to resp's peer */
            /* Actually new_get will be scanned by hwm */
        }
        return 1;
    }

    /* ── LEN: len list ─────────────────────────────────────────────────── */
    if (a->tag == TAG_LEN || b->tag == TAG_LEN) {
        uint32_t len_nid = (a->tag == TAG_LEN) ? ai : bi;
        Node *op_n = &net->nodes[len_nid];
        uint32_t lp = op_n->ports[1], resp = op_n->ports[2];
        if (lp == NULL_PORT || resp == NULL_PORT) return 0;
        uint32_t cur = port_node(lp);
        int64_t n = 0;
        while (cur < net->count && net->nodes[cur].alive && net->nodes[cur].tag == TAG_CON) {
            n++;
            uint32_t tp = net->nodes[cur].ports[2];
            if (tp == NULL_PORT) break;
            cur = port_node(tp);
        }
        if (cur >= net->count || !net->nodes[cur].alive ||
            (net->nodes[cur].tag != TAG_NIL && net->nodes[cur].tag != TAG_CON)) return 0;
        erase(net, port_node(lp));
        net_kill(net, len_nid);
        uint32_t out = net_alloc(net, TAG_INT, 0, n);
        net_link(net, out * MAX_PORTS + 0, resp);
        return 1;
    }

    /* ── APPEND: l ++ r ────────────────────────────────────────────────── */
    if (a->tag == TAG_APPEND || b->tag == TAG_APPEND) {
        uint32_t op_nid = (a->tag == TAG_APPEND) ? ai : bi;
        Node *op_n = &net->nodes[op_nid];
        uint32_t lp = op_n->ports[1], rp = op_n->ports[2], resp = op_n->ports[3];
        if (lp == NULL_PORT || rp == NULL_PORT || resp == NULL_PORT) return 0;
        uint32_t lroot = port_node(lp);
        if (lroot >= net->count || !net->nodes[lroot].alive) return 0;
        if (net->nodes[lroot].tag == TAG_NIL) {
            net_kill(net, lroot);
            net_kill(net, op_nid);
            net_link(net, rp, resp);
            return 1;
        }
        if (net->nodes[lroot].tag != TAG_CON) return 0;
        uint32_t cur = lroot;
        while (1) {
            uint32_t tp = net->nodes[cur].ports[2];
            if (tp == NULL_PORT) return 0;
            uint32_t nxt = port_node(tp);
            if (nxt >= net->count || !net->nodes[nxt].alive) return 0;
            if (net->nodes[nxt].tag == TAG_NIL) {
                net_kill(net, nxt);
                net_link(net, cur * MAX_PORTS + 2, rp);
                net_kill(net, op_nid);
                net_link(net, lroot * MAX_PORTS + 0, resp);
                return 1;
            }
            if (net->nodes[nxt].tag != TAG_CON) return 0;
            cur = nxt;
        }
    }

    /* ── TAKE: take n list ─────────────────────────────────────────────── */
    if (a->tag == TAG_TAKE || b->tag == TAG_TAKE) {
        uint32_t op_nid = (a->tag == TAG_TAKE) ? ai : bi;
        Node *op_n = &net->nodes[op_nid];
        uint32_t np = op_n->ports[1], lp = op_n->ports[2], resp = op_n->ports[3];
        if (np == NULL_PORT || lp == NULL_PORT || resp == NULL_PORT) return 0;
        uint32_t nn = port_node(np), lroot = port_node(lp);
        if (nn >= net->count || lroot >= net->count) return 0;
        if (!net->nodes[nn].alive || !net->nodes[lroot].alive) return 0;
        if (net->nodes[nn].tag != TAG_INT) return 0;
        int64_t n = net->nodes[nn].meta;
        net_kill(net, nn);
        if (n <= 0) {
            erase(net, lroot);
            uint32_t nil = net_alloc(net, TAG_NIL, 0, 0);
            net_kill(net, op_nid);
            net_link(net, nil * MAX_PORTS + 0, resp);
            return 1;
        }
        uint32_t cur = lroot;
        int64_t k = n;
        while (k > 1 && net->nodes[cur].tag == TAG_CON) {
            uint32_t tp = net->nodes[cur].ports[2];
            if (tp == NULL_PORT) break;
            uint32_t nxt = port_node(tp);
            if (nxt >= net->count || !net->nodes[nxt].alive) break;
            if (net->nodes[nxt].tag != TAG_CON) break;
            cur = nxt;
            k--;
        }
        if (net->nodes[cur].tag == TAG_CON) {
            uint32_t old_tail = net->nodes[cur].ports[2];
            uint32_t old_tail_n = port_node(old_tail);
            if (old_tail != NULL_PORT && old_tail_n < net->count && net->nodes[old_tail_n].alive) {
                uint32_t nil = net_alloc(net, TAG_NIL, 0, 0);
                net_link(net, cur * MAX_PORTS + 2, nil * MAX_PORTS + 0);
                erase(net, old_tail_n);
            }
        }
        net_kill(net, op_nid);
        net_link(net, lroot * MAX_PORTS + 0, resp);
        return 1;
    }

    /* ── DROP: drop n list ─────────────────────────────────────────────── */
    if (a->tag == TAG_DROP || b->tag == TAG_DROP) {
        uint32_t op_nid = (a->tag == TAG_DROP) ? ai : bi;
        Node *op_n = &net->nodes[op_nid];
        uint32_t np = op_n->ports[1], lp = op_n->ports[2], resp = op_n->ports[3];
        if (np == NULL_PORT || lp == NULL_PORT || resp == NULL_PORT) return 0;
        uint32_t nn = port_node(np), cur = port_node(lp);
        if (nn >= net->count || cur >= net->count) return 0;
        if (!net->nodes[nn].alive || !net->nodes[cur].alive) return 0;
        if (net->nodes[nn].tag != TAG_INT) return 0;
        int64_t n = net->nodes[nn].meta;
        net_kill(net, nn);
        while (n > 0 && cur < net->count && net->nodes[cur].alive && net->nodes[cur].tag == TAG_CON) {
            uint32_t hp = net->nodes[cur].ports[1];
            uint32_t tp = net->nodes[cur].ports[2];
            if (hp != NULL_PORT) erase(net, port_node(hp));
            net_kill(net, cur);
            if (tp == NULL_PORT) break;
            cur = port_node(tp);
            n--;
        }
        net_kill(net, op_nid);
        if (cur >= net->count || !net->nodes[cur].alive) {
            uint32_t nil = net_alloc(net, TAG_NIL, 0, 0);
            net_link(net, nil * MAX_PORTS + 0, resp);
        } else {
            net_link(net, cur * MAX_PORTS + 0, resp);
        }
        return 1;
    }

    /* ── ARR: array n v ────────────────────────────────────────────────── */
    if (a->tag == TAG_ARR || b->tag == TAG_ARR) {
        uint32_t op_nid = (a->tag == TAG_ARR) ? ai : bi;
        Node *op_n = &net->nodes[op_nid];
        uint32_t np = op_n->ports[1], vp = op_n->ports[2], resp = op_n->ports[3];
        if (np == NULL_PORT || vp == NULL_PORT || resp == NULL_PORT) return 0;
        uint32_t nn = port_node(np), vn = port_node(vp);
        if (nn >= net->count || vn >= net->count) return 0;
        if (!net->nodes[nn].alive || !net->nodes[vn].alive) return 0;
        if (net->nodes[nn].tag != TAG_INT) return 0;
        int64_t n = net->nodes[nn].meta;
        if (n <= 0) {
            erase(net, vn);
            net_kill(net, nn);
            net_kill(net, op_nid);
            uint32_t nil = net_alloc(net, TAG_NIL, 0, 0);
            net_link(net, nil * MAX_PORTS + 0, resp);
            return 1;
        }
        uint32_t first = UINT32_MAX, prev = UINT32_MAX;
        for (int64_t i = 0; i < n; i++) {
            uint32_t c = net_alloc(net, TAG_CON, 2, 0);
            uint32_t e = clone_value_atom(net, &net->nodes[vn]);
            if (e == UINT32_MAX) {
                /* non-atom array fill not supported yet */
                return 0;
            }
            net_link(net, c * MAX_PORTS + 1, e * MAX_PORTS + 0);
            if (prev != UINT32_MAX) net_link(net, prev * MAX_PORTS + 2, c * MAX_PORTS + 0);
            else first = c;
            prev = c;
        }
        uint32_t nil = net_alloc(net, TAG_NIL, 0, 0);
        net_link(net, prev * MAX_PORTS + 2, nil * MAX_PORTS + 0);
        erase(net, vn);
        net_kill(net, nn);
        net_kill(net, op_nid);
        net_link(net, first * MAX_PORTS + 0, resp);
        return 1;
    }

    /* ── AST: aset list idx value ──────────────────────────────────────── */
    if (a->tag == TAG_AST || b->tag == TAG_AST) {
        uint32_t op_nid = (a->tag == TAG_AST) ? ai : bi;
        Node *op_n = &net->nodes[op_nid];
        uint32_t lp = op_n->ports[1], np = op_n->ports[2], vp = op_n->ports[3], resp = op_n->ports[4];
        if (lp == NULL_PORT || np == NULL_PORT || vp == NULL_PORT || resp == NULL_PORT) return 0;
        uint32_t cur = port_node(lp), nn = port_node(np);
        if (cur >= net->count || nn >= net->count) return 0;
        if (!net->nodes[cur].alive || !net->nodes[nn].alive) return 0;
        if (net->nodes[nn].tag != TAG_INT) return 0;
        int64_t idx = net->nodes[nn].meta;
        if (idx < 0) return 0;
        while (idx > 0 && cur < net->count && net->nodes[cur].alive && net->nodes[cur].tag == TAG_CON) {
            uint32_t tp = net->nodes[cur].ports[2];
            if (tp == NULL_PORT) break;
            cur = port_node(tp);
            idx--;
        }
        if (cur >= net->count || !net->nodes[cur].alive || net->nodes[cur].tag != TAG_CON) return 0;
        uint32_t old_h = net->nodes[cur].ports[1];
        if (old_h != NULL_PORT) erase(net, port_node(old_h));
        net_link(net, cur * MAX_PORTS + 1, vp);
        net_kill(net, nn);
        net_kill(net, op_nid);
        net_link(net, lp, resp);
        return 1;
    }

    /* ── FILTER: [x <- list | x P pivot] ──────────────────────────────── */
    if (a->tag == TAG_FILT_LE || a->tag == TAG_FILT_GT || a->tag == TAG_FILT_LT ||
        a->tag == TAG_FILT_GE || a->tag == TAG_FILT_EQ ||
        b->tag == TAG_FILT_LE || b->tag == TAG_FILT_GT || b->tag == TAG_FILT_LT ||
        b->tag == TAG_FILT_GE || b->tag == TAG_FILT_EQ) {
        uint32_t op_nid = (a->tag >= TAG_FILT_LE && a->tag <= TAG_FILT_EQ) ? ai : bi;
        Node *op_n = &net->nodes[op_nid];
        uint8_t pred = op_n->tag;
        uint32_t pp = op_n->ports[1], lp = op_n->ports[2], resp = op_n->ports[3];
        if (pp == NULL_PORT || lp == NULL_PORT || resp == NULL_PORT) return 0;
        uint32_t pn = port_node(pp), cur = port_node(lp);
        if (pn >= net->count || cur >= net->count) return 0;
        if (!net->nodes[pn].alive || !net->nodes[cur].alive) return 0;

        uint32_t out_head = UINT32_MAX;
        uint32_t prev_kept = UINT32_MAX;

        while (cur < net->count && net->nodes[cur].alive && net->nodes[cur].tag == TAG_CON) {
            uint32_t hp = net->nodes[cur].ports[1];
            uint32_t tp = net->nodes[cur].ports[2];
            uint32_t hn = port_node(hp);
            int keep = (hn < net->count && net->nodes[hn].alive) ?
                cmp_filter_pred(pred, &net->nodes[hn], &net->nodes[pn]) : 0;
            uint32_t next = (tp == NULL_PORT) ? UINT32_MAX : port_node(tp);
            if (keep) {
                if (out_head == UINT32_MAX) out_head = cur;
                if (prev_kept != UINT32_MAX) net_link(net, prev_kept * MAX_PORTS + 2, cur * MAX_PORTS + 0);
                prev_kept = cur;
            } else {
                if (hp != NULL_PORT) erase(net, hn);
                net_kill(net, cur);
            }
            if (next == UINT32_MAX || next >= net->count || !net->nodes[next].alive) break;
            if (net->nodes[next].tag == TAG_NIL) {
                if (prev_kept != UINT32_MAX) net_link(net, prev_kept * MAX_PORTS + 2, next * MAX_PORTS + 0);
                else out_head = next;
                break;
            }
            cur = next;
        }

        erase(net, pn);
        net_kill(net, op_nid);
        if (out_head == UINT32_MAX) {
            uint32_t nil = net_alloc(net, TAG_NIL, 0, 0);
            net_link(net, nil * MAX_PORTS + 0, resp);
        } else {
            net_link(net, out_head * MAX_PORTS + 0, resp);
        }
        return 1;
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
        fprintf(stderr, "Usage: nelac <file.nelac> [--disasm|--game]\n");
        return 1;
    }

    int disasm = (argc >= 3 && strcmp(argv[2], "--disasm") == 0);
    int game   = (argc >= 3 && strcmp(argv[2], "--game")   == 0);

    Net net;
    uint32_t root = load_nelac(argv[1], &net);

    if (disasm) {
        printf("NELAC  nodes=%u  root=%u\n", net.count, root);
        for (uint32_t i = 0; i < net.count; i++) {
            Node *n = &net.nodes[i];
            if (!n->alive) continue;
            /* tag name */
            static const char *names[256] = {
                [TAG_CON]="CON",[TAG_DUP]="DUP",[TAG_ERA]="ERA",
                [TAG_APP]="APP",[TAG_LAM]="LAM",
                [TAG_INT]="INT",[TAG_FLT]="FLT",[TAG_STR]="STR",
                [TAG_BOO]="BOO",[TAG_PAR]="PAR",
                [TAG_VAR]="VAR",[TAG_FIX]="FIX",[TAG_IOT]="IOT",
                [TAG_IOKEY]="IOKEY",[TAG_IOPRT]="IOPRT",[TAG_FREF]="FREF",
                [TAG_MAT]="MAT",[TAG_FST]="FST",[TAG_SND]="SND",
                [TAG_ADD]="ADD",[TAG_SUB]="SUB",[TAG_MUL]="MUL",
                [TAG_DIV]="DIV",[TAG_MOD]="MOD",[TAG_NEG]="NEG",
                [TAG_EQL]="EQL",[TAG_LTH]="LTH",[TAG_LEQ]="LEQ",
                [TAG_GTH]="GTH",[TAG_GEQ]="GEQ",
                [TAG_AND]="AND",[TAG_ORR]="ORR",[TAG_NOT]="NOT",
                [TAG_IFT]="IFT",[TAG_NIL]="NIL",[TAG_HED]="HED",
                [TAG_TAL]="TAL",[TAG_GET]="GET",[TAG_LEN]="LEN",
                [TAG_ARR]="ARR",[TAG_AST]="AST",
            };
            const char *tname = names[n->tag] ? names[n->tag] : "???";
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

    /* ── GPU Framebuffer Architecture (v0.10+) ─────────────────────────────
     *
     * DESIGN: Match Python's clean separation:
     *   NELA-C computation (interaction net reduction)
     *       ↓
     *   Render data: list-of-lists of shade integers (0-4)
     *       ↓
     *   Host GPU backend (Vulkan/Metal/OpenGL/WebGL)
     *
     * NOT YET IMPLEMENTED in C runtime (known blocker: scheduler doesn't reach
     * I/O frontier in unreduced game graphs).  When fixed:
     *
     *   1. io_print_frame(frame_data) callback
     *      - Input: interaction net representing list[list[int]] (shade grid)
     *      - Extract frame structure (40×21 grid of shade values 0-4)
     *      - Call gpu_render_frame(frame)
     *
     *   2. gpu_render_frame(frame) — GPU backend stub
     *      - Each cell (col, row) has shade integer
     *      - shade_colors[shade] = RGB triplet (0=dark gray → 4=white)
     *      - Draw pixel_size × pixel_size rectangle at (col*pixel_size, row*pixel_size)
     *      - Options:
     *        a) Pygame SDL2 backend (Python-style texture upload)
     *        b) Vulkan/Metal via native GPU API (fastest, platform-specific)
     *        c) WebGL if compiled to WASM
     *      - Target: 30 FPS (same as Python)
     *
     *   3. io_getch() callback — unified keyboard input
     *      - Input: Waits for keyboard input
     *      - Output: Single ASCII char (w/a/s/d/e/q, or empty)
     *      - Could read from:
     *        a) Terminal raw mode (current, fallback)
     *        b) GPU framework event loop (Pygame, GLFW, Vulkan WSI)
     *      - Should not block; return empty string if no key available
     *
     * CURRENT STATUS:
     *   - Python: Fully implemented (GPU + terminal fallback) ✓
     *   - C: Terminal mode only, GPU stubs needed
     *   - Blocker: C scheduler doesn't reach I/O frontier in game nets
     *   - When scheduler fixed, GPU path will work automatically
     *
     * Future extensions:
     *   - Texture mapping (shader-based wall/sprite tiling)
     *   - Lighting calculations pushed into GPU (fragment shaders)
     *   - Parallel raycasting on GPU compute shaders
     * ─────────────────────────────────────────────────────────────────────── */
    if (game) {
        printf("\033[2J\033[H");  /* clear screen */
        fflush(stdout);
        io_raw_on();
        g_io_enabled = 1;
    }

    /* Reduce (no-op for v0.10 normal-form nets; full SIC for v0.11 nets) */
    reduce(&net, root);

    if (game) {
        io_raw_off();
        printf("\nBye!\n");
    } else {
        /* Print result */
        print_value(&net, root, 0);
        printf("\n");
    }

    net_free(&net);
    return 0;
}
