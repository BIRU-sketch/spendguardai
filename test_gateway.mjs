import OpenAI from "openai";

// Matches your actual route (no /v1 prefix) — same base_url shape you're
// already using successfully in test_gateway.py.
const client = new OpenAI({
  baseURL: "http://localhost:8000",
  apiKey: "sk-test-local",
});

async function main() {
  const response = await client.chat.completions.create({
    model: "inclusionai/ling-3.0-flash-vl:free",
    messages: [{ role: "user", content: "Say hello in exactly 3 words" }],
  });

  console.log(response.choices[0].message.content);
  console.log(response.usage);
}

main();