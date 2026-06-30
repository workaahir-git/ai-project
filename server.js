const express = require("express");
const cors = require("cors");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

// Health check — Render pings this to confirm the service is up
app.get("/", (req, res) => {
  res.send("GymCoach Studio backend is running.");
});

// Main route — receives prompt from dashbord.html, calls Grok, returns plan
app.post("/api/generate", async (req, res) => {
  const { prompt } = req.body;

  if (!prompt) {
    return res.status(400).json({ error: "No prompt provided." });
  }

  const GROK_API_KEY = process.env.GROK_API_KEY;

  if (!GROK_API_KEY) {
    return res.status(500).json({ error: "GROK_API_KEY not set in environment variables." });
  }

  try {
    const grokRes = await fetch("https://api.x.ai/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${GROK_API_KEY}`,
      },
      body: JSON.stringify({
        model: "grok-3",          // swap to "grok-3-mini" for cheaper calls
        max_tokens: 4000,
        messages: [
          {
            role: "system",
            content: "You are an expert Indian fitness coach and sports nutritionist. Always respond in clean, structured plain text with clear headings and sections. Be specific, practical, and detailed."
          },
          {
            role: "user",
            content: prompt
          }
        ]
      }),
    });

    if (!grokRes.ok) {
      const errText = await grokRes.text();
      console.error("Grok API error:", errText);
      return res.status(502).json({ error: "Grok API call failed.", detail: errText });
    }

    const data = await grokRes.json();
    const planText = data.choices?.[0]?.message?.content || "No response from Grok.";

    return res.json({ plan: planText });

  } catch (err) {
    console.error("Server error:", err);
    return res.status(500).json({ error: "Internal server error.", detail: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`GymCoach backend running on port ${PORT}`);
});
