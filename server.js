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

// Main route — receives prompt from dashbord.html, calls Groq, returns plan
app.post("/api/generate", async (req, res) => {
  const { prompt } = req.body;

  if (!prompt) {
    return res.status(400).json({ error: "No prompt provided." });
  }

  const GROQ_API_KEY = process.env.GROQ_API_KEY;

  console.log("KEY CHECK:", GROQ_API_KEY ? `present, length ${GROQ_API_KEY.length}` : "MISSING");

  if (!GROQ_API_KEY) {
    return res.status(500).json({ error: "GROQ_API_KEY not set in environment variables." });
  }

  try {
    const groqRes = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${GROQ_API_KEY}`,
      },
      body: JSON.stringify({
        model: "llama-3.3-70b-versatile", // matches the model already used by the Python/Groq backend
        max_tokens: 4000,
        temperature: 0,
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

    if (!groqRes.ok) {
      const errText = await groqRes.text();
      console.error("Groq API error:", errText);
      return res.status(502).json({ error: "Groq API call failed.", detail: errText });
    }

    const data = await groqRes.json();
    const planText = data.choices?.[0]?.message?.content || "No response from Groq.";

    return res.json({ plan: planText });

  } catch (err) {
    console.error("Server error:", err);
    return res.status(500).json({ error: "Internal server error.", detail: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`GymCoach backend running on port ${PORT}`);
});
