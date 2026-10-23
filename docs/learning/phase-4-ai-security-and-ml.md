# Phase 4: AI security and a trained model, in plain language

What was run: `python -m frontdesk_ml.training_data` (training rows), `python -m
frontdesk_ml.classifier compare` (trains, picks a threshold on dev, writes
`docs/evidence/classifier-comparison.md`), `python -m frontdesk.drills model-down` (the outage
drill), and the full test suite, which now includes the classifier, the drill and the
AI-security mapping.

## 1. A trained classifier, and why it does not make decisions

The question was whether a trained model beats the hand-written rules at spotting "this
customer needs a person". The model is three logistic regressions on hashed character n-grams,
trained with scikit-learn and exported as plain numbers, so the Lambda runs it with numpy in
under a millisecond. It was trained on 380 synthetic, template-made sentences.

The rules won on both evaluation sets (holdout needs-person F1: rules 0.80, model 0.00 at the
threshold chosen on dev). The model is not useless: it ranks well (ROC AUC 0.96 on the
holdout), and it scored two of the three handoffs the rules missed above every true
non-handoff. But the dev split had no rule misses at all, so there was no honest way to choose
a threshold that captures that, and choosing one on the holdout would be cheating.

> Interview line: "I trained the model, it lost to the rules, so it runs in shadow mode: every
> real message logs what it would have done, which builds the dataset that decides whether it
> ever gets to act."

Check yourself: why can't you pick the classifier's threshold on the holdout set, even if it makes the numbers better?

## 2. The leak the test caught

The first training set shared twelve sentences with the evaluation sets ("Can I pay by card?",
"PERSON"). A model tested on sentences it trained on looks better than it is: test F1 went from
0.42 to 0.12 once the leak was removed. A test now fails the build if any evaluation question
appears in the training data, another regenerates the data from its seed, and a third rebuilds
the committed weights from the committed data.

> Interview line: "My leakage test caught a real leak in my own training data, and the honest
> number was a third of the flattering one. That test now runs on every pull request."

Check yourself: name two ways test data can leak into training other than identical sentences.

## 3. Shadow mode done safely

The shadow prediction is computed on the *redacted* text, stores no text (only a probability,
labels and the model version), never changes the reply, and if it crashes the error is noted
and the customer is answered anyway. Each of those four properties has its own test.

> Interview line: "A shadow model is an experiment running in production, so it gets the same
> rules as production code: redacted input, no text in logs, and it can't take the service down."

Check yourself: what would go wrong if the shadow prediction were computed on the raw message?

## 4. Mapping controls to OWASP and MITRE ATLAS

`docs/AI-SECURITY.md` lists the fourteen layers a message passes through, then maps all ten
OWASP LLM risks (2025) and twelve ATLAS techniques to those layers, with the test or red-team
case that proves each. The ATLAS names were taken from MITRE's published data file, not from
memory. A test fails if the red-team suite uses an id the page does not map, or the page cites
a red-team case that does not exist. Two cases for improper output handling (LLM05) were added
because the suite had none: markup injection and a smuggled payment link. Both are stopped.

> Interview line: "Every OWASP LLM risk maps to a control and a piece of evidence, and the
> mapping is checked by a test, so it can't drift from the attack suite."

Check yourself: which OWASP LLM risk is handled by CI gates rather than by red-team prompts, and why?

## 5. The outage drill

The drill breaks the model two ways (Bedrock throttling, and a plain timeout the agent does not
expect), then checks ten things: the customer's reply, the named assignee, STOP, duplicate
suppression, the workflow's Catch, the inbox queue, a staff reply, the owner's dashboard, the
alarm metric, and recovery. It runs on every pull request. An AWS version denies Bedrock to the
Lambda roles in dev and watches DET-06 and DET-02 fire.

> Interview line: "The timeout case taught me the agent's own error handling isn't enough; the
> Step Functions Catch is the real safety net."

Check yourself: why does the AWS drill use an IAM deny rather than changing the model id in an environment variable?

## 6. Tool calls in the event stream

Every tool the agent calls is now recorded in the conversation event, by name and outcome only.
Arguments and results stay out, because they can contain an order number or an address, and
the event stream must stay free of customer details.

> Interview line: "The event stream answers 'what did the bot do' without becoming a second
> copy of the customer's data."

Check yourself: which test would fail if someone added the tool arguments to the payload?
