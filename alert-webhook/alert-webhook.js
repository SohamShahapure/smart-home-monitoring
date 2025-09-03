/*  Smart-Home alert webhook receiver
 *  Listens on /webhook/alerts  and  /webhook/warnings
 */
const express = require('express');
const app = express();
const PORT = process.env.PORT || 3001;

app.use(express.json());

function logEvent(label, body) {
  const pretty = JSON.stringify(body, null, 2);
  console.log(`${label} RECEIVED:\n${pretty}\n---`);
}

/* Critical / default alerts */
app.post('/webhook/alerts', (req, res) => {
  logEvent('🚨 ALERT', req.body);
  // TODO: forward to Slack / email etc.
  res.status(200).json({ status: 'received' });
});

/* Warning-level alerts */
app.post('/webhook/warnings', (req, res) => {
  logEvent('⚠️  WARNING', req.body);
  res.status(200).json({ status: 'received' });
});

/* Liveness probe */
app.get('/health', (_, res) => res.send('OK'));

app.listen(PORT, () =>
  console.log(`Alert-webhook server listening on port ${PORT}`)
);
