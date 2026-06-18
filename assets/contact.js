(function () {
  var recipient = "animetroconsulting@gmail.com";

  function field(form, name) {
    var element = form.elements[name];
    return element ? element.value.trim() : "";
  }

  function encodeMailtoComponent(value) {
    return encodeURIComponent(value).replace(/%0A/g, "%0D%0A");
  }

  function formatBody(form, language) {
    var name = field(form, "name");
    var email = field(form, "email");
    var grade = field(form, "grade");
    var message = field(form, "message");

    if (language === "zh") {
      return [
        "姓名：" + name,
        "電子郵件：" + email,
        "學生年級：" + grade,
        "諮詢需求：",
        message
      ].join("\n");
    }

    return [
      "Name: " + name,
      "Email: " + email,
      "Student Grade: " + grade,
      "Message:",
      message
    ].join("\n");
  }

  function subjectFor(language) {
    return language === "zh"
      ? "艾美加教育顾问私人諮詢"
      : "Animetro Consulting Private Consultation";
  }

  document.querySelectorAll("[data-contact-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();

      var language = form.getAttribute("data-language") || "en";
      var subject = encodeMailtoComponent(subjectFor(language));
      var body = encodeMailtoComponent(formatBody(form, language));

      window.location.href = "mailto:" + recipient + "?subject=" + subject + "&body=" + body;
    });
  });
})();
