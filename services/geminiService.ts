import { GoogleGenAI, GenerateContentResponse, Content } from "@google/genai";
import { REGULATIONS_DATA } from '../data';

// Initialize the Gemini client
// The API key is obtained from the environment variable process.env.API_KEY
const ai = new GoogleGenAI({ apiKey: process.env.API_KEY });

// System instruction to guide the model
// We provide a simplified version of the regulations data to avoid exceeding token limits
const SYSTEM_INSTRUCTION = `
You are an expert consultant for JAF (Japan Automobile Federation) Motor Sports Regulations.
You have access to a database of regulation documents.
Your goal is to assist users in finding the correct regulation document for their inquiries.

Here is the list of available regulations in JSON format:
${JSON.stringify(REGULATIONS_DATA.map(r => ({ title: r.title, category: r.category, summary: r.summary, url: r.url })))}

Rules:
1. Always reference the specific "title" of the regulation when answering.
2. If the user asks about a specific topic (e.g., "rally seatbelts"), search your internal knowledge of the provided list summaries and titles to suggest the most relevant document.
3. Provide the direct URL to the PDF if a match is found.
4. If the answer is not in the provided list, state that you are limited to the provided dataset but provide general motorsport knowledge if applicable, while clarifying it might not be the latest JAF rule.
5. Be polite and professional.
6. Answer in Japanese as the user is likely Japanese.
7. Keep responses concise and helpful.
8. Use Markdown formatting (bolding, lists) to make the answer easy to read.
`;

export const streamMessageToGemini = async function* (message: string, history: Content[]) {
  try {
    // Using gemini-3-flash-preview for basic text tasks as per guidelines
    const model = "gemini-3-flash-preview"; 

    // Create a chat session with the provided history
    const chat = ai.chats.create({
      model: model,
      config: {
        systemInstruction: SYSTEM_INSTRUCTION,
      },
      history: history,
    });

    // Send the user's new message with streaming
    const responseStream = await chat.sendMessageStream({
        message: message
    });

    for await (const chunk of responseStream) {
        const response = chunk as GenerateContentResponse;
        if (response.text) {
            yield response.text;
        }
    }
  } catch (error) {
    console.error("Gemini API Error:", error);
    yield "エラーが発生しました。しばらく待ってからもう一度お試しください。";
  }
};