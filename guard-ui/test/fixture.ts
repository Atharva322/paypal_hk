import { attach } from "../src/index";

const result = document.getElementById("result")!;

const buttons = attach(
  {
    onApprove: () => {
      result.textContent = "store captured around the guard";
    },
  },
  {
    apiBase: "/api",
    returnFocus: () => result,
    onResult: (outcome) => {
      result.textContent = JSON.stringify(outcome);
    },
    onError: (error) => {
      result.textContent = `error: ${error.message}`;
    },
  },
);

document.getElementById("pay")!.addEventListener("click", () => {
  void buttons.onApprove({ orderID: "ORDER1" });
});
