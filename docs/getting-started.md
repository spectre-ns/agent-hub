# Getting started

This takes a fresh clone to a running demo practice with agents working in it. It needs Python 3.12 or later, Docker for
the database (or a MongoDB 7 replica set of your own), and [Ollama](https://ollama.com) with `qwen3.5:4b` pulled for
the agents to answer with (`ollama pull qwen3.5:4b`; step 3 says how to do without). It sees pictures too: the
accounting office's documents agent reads photos of slips with it.

## 1. Get the code

```sh
git clone https://github.com/spectre-ns/agent-hub.git
cd agent-hub
python3 -m venv .venv
. .venv/bin/activate
pip install -e .            # the hub is the standard library, pymongo and pypdf
```

That puts the `practicehub` command on your path. `python -m practicehub.cli` is the same command.

## 2. Start MongoDB

The hub keeps everything in MongoDB, and writes in transactions, so the server must be a replica set. One node will
do:

```sh
docker run -d --name practicehub-mongo -p 127.0.0.1:27017:27017 mongo:7.0 --replSet rs0 --bind_ip_all
docker exec practicehub-mongo mongosh --quiet --eval \
  'rs.initiate({_id: "rs0", members: [{_id: 0, host: "localhost:27017"}]})'
```

If the second command says the connection was refused, the server is still starting: run it again.

The hub looks for `mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true` by default. To use another
server, set `PRACTICEHUB_DATABASE` to its URI. The database is named `practicehub` unless the URI names one or
`PRACTICEHUB_DATABASE_NAME` does.

## 3. Set up the demo practice

```sh
practicehub init --demo            # Harbourview Family Medicine, a fictional clinic, with demo patients and open work
practicehub agents run --once      # the agents take what is theirs in the inbox, then stop
practicehub verify                 # check the track: its hash chain, seals and signatures
```

`init` prints what it set up (13 members, 9 workflows). Each agent is given the hub's default model, a local model
through Ollama (`ollama/qwen3.5:4b`), written into its profile. If Ollama is not running, the agents pass their
work to people, saying the model could not be reached. To try the demo with no model at all, set
`PRACTICEHUB_DEFAULT_MODEL=standin` before `init`: the agents then answer with **stand-ins**, fixed, deterministic
answers that ship with the pack. `verify` should end with "chain, seals and signatures intact".

The hub also writes a `state/` directory beside you. It holds the key that signs the track (`state/.secret`) and
the track's anchors. It is gitignored; do not commit it.

## 4. Open it

```sh
practicehub serve --dev --agents   # http://127.0.0.1:8200/
```

`--dev` lets you sign in as any member without a passphrase, and only listens on this machine. `--agents` runs the
agents in the same process, so they take new work as it appears.

Sign in as:

- **Dr. Amara Okafor**, to edit and sign the note the scribe agent drafted;
- **Jo** (front desk), to see what someone without clinical access sees;
- **Rana** (practice manager), to change settings and propose a workflow change;
- **Sam**, to submit the claim the billing agent coded.

Things to try:

1. Open your inbox and take a piece of work. The work is done on its record's page, where History shows every step
   so far and who did it, person or agent.
2. Open **Workflows** and pick one. Its page says, in words and as a diagram, how the work moves.
3. As Rana, open **Edit workflow**, change something and save. Saving is a proposal. Sign in as Dr. Amara to see
   the diff and approve it: a change takes two policy owners.
4. Open **Team** and pick an agent. It has a sponsor, roles, skills, a risk limit and a kill switch.
5. Open **Track**. Every event names the event that caused it.

## 5. Try another practice

The same core runs other kinds of practice. Each hub has its own database and its own state directory, so give a
second one both:

```sh
# a clinic with document control, quality, a wiki and stock (an overlay: see the customization guide)
PRACTICEHUB_DATABASE_NAME=harbourview practicehub --state state-harbourview init --pack harbourview --demo

# an accounting office: personal tax, bookkeeping, corporate year-end, GST/HST and payroll
PRACTICEHUB_DATABASE_NAME=accounting practicehub --state state-accounting init --pack accounting-office --demo
```

Serve one by passing the same two settings to `serve`:

```sh
PRACTICEHUB_DATABASE_NAME=accounting practicehub --state state-accounting serve --dev --agents --port 8201
```

`init --force` replaces the hub in the database it is pointed at. It **drops that database**. `--state` does not
choose the database; `PRACTICEHUB_DATABASE` and `PRACTICEHUB_DATABASE_NAME` do.

## 6. Change an agent's model

An agent's profile names its model as `<backend>/<model>`:

| Model | What answers |
|---|---|
| `ollama/<model>` | a local model through Ollama (`OLLAMA_URL`, default `http://localhost:11434`). A new agent is given `PRACTICEHUB_DEFAULT_MODEL`, else `ollama/qwen3.5:4b`. Every call asks Ollama for a 32k-token window (`OLLAMA_NUM_CTX`). The hub sets no temperature or other sampling, so each model answers with the settings it ships with: greedy decoding (temperature 0) makes a model that thinks repeat itself. A model thinks before it answers only with `OLLAMA_THINK=1`; its thinking is shown as it comes, folded under the agent's work in the record's History and under each answer in the chat, and a call whose thinking goes round in a loop is stopped |
| `standin` | the pack's fixed answers, with no model |
| `litellm/<model>` | a model behind your LiteLLM proxy (`LITELLM_URL`, `LITELLM_API_KEY`) |
| `anthropic/<model>` | Claude through the Anthropic API (`ANTHROPIC_API_KEY`). It sends data off the premises, so it is refused unless `PRACTICEHUB_ALLOW_EXTERNAL_MODELS=1` |

On a GPU with 8 GB, Ollama keeps more of a model on the GPU when its context cache is 8-bit: start the Ollama server
with `OLLAMA_KV_CACHE_TYPE=q8_0` (it needs flash attention, which Ollama turns on for the models that support it). With
the Ollama snap: `sudo snap set ollama kv-cache-type=q8_0`, then `sudo snap restart ollama`.

A profile is under change control, like everything else an agent may do. One policy owner proposes the change and
another approves it:

```sh
practicehub member profile scribe-agent --model ollama/qwen3.5:4b --as rana
practicehub member approve-profile scribe-agent 2 --as dr-amara    # 2: the version the first command printed
```

The agent's page under **Team** does the same in the browser. There the model is picked from a list of the models
the hub can reach now: what Ollama has installed, what your LiteLLM proxy serves (when `LITELLM_URL` is set), Claude's
models (when models off the premises are allowed), and `standin`. The model for pictures lists only those that can
see. A server that does not answer is named under the list.

## 7. Run it for real

Outside `--dev`, people sign in with their member id and a passphrase, and the hub should get its signing key from
the environment (`PRACTICEHUB_SECRET`), not from `state/.secret`.

`deploy/docker-compose.yml` runs the hub with its MongoDB:

```sh
cp deploy/env.example deploy/.env      # then set PRACTICEHUB_SECRET in it
docker compose -f deploy/docker-compose.yml run --rm hub init --pack general-practice
docker compose -f deploy/docker-compose.yml up hub
```

The first member admin issues their own one-time setup code from the command line, and sets a passphrase with it at
`/setup`. After that, they issue codes for everyone else from the Team page:

```sh
docker compose -f deploy/docker-compose.yml run --rm hub member passphrase-link rana --as rana
```

The README covers the rest of the stack (Keycloak, OpenFGA, LiteLLM, Collabora, Kubernetes), and backup and
restore.

## 8. Run the tests

```sh
pip install -e '.[dev]'
scripts/test-mongo.sh        # a one-node replica set in Docker on 127.0.0.1:27118; every test gets its own database
python -m pytest -q
```

## Where next

- [Customizing Practice Hub](customizing.md): make it your own practice, from the settings pages to a pack of your
  own.
- [Writing workflows](workflows.md): the reference for workflows, blocks, conditions and risk.
- [Packs](packs.md): setting up another kind of practice.
- [The booking portal](portal.md): the public pages for the people a practice serves.
- `AGENTS.md`: ground rules for changing the core, and where each rule lives in the code.
