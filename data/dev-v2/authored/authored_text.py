"""Original prose, code, glossary and table content for the structure documents."""

# ruff: noqa: E501  (content tables: long literal lines are the data)

from __future__ import annotations

import random

# ----------------------------------------------------------------- code snippets
# (language, text). Short, original, ASCII only, lines under 62 characters.

CODE: list[tuple[str, str]] = [
    (
        "python",
        """def bisect(f, lo, hi, tol=1e-9):
    # f(lo) and f(hi) must have opposite signs
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2""",
    ),
    (
        "python",
        """def trapezoid(f, a, b, n=100):
    h = (b - a) / n
    total = (f(a) + f(b)) / 2
    for k in range(1, n):
        total += f(a + k * h)
    return total * h""",
    ),
    (
        "python",
        """def newton(f, df, x, steps=8):
    for _ in range(steps):
        x = x - f(x) / df(x)
    return x

print(newton(lambda t: t * t - 2, lambda t: 2 * t, 1.0))""",
    ),
    (
        "python",
        """def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

print(gcd(84, 36))""",
    ),
    (
        "python",
        """def sieve(n):
    flags = [True] * (n + 1)
    flags[0:2] = [False, False]
    for p in range(2, int(n ** 0.5) + 1):
        if flags[p]:
            for m in range(p * p, n + 1, p):
                flags[m] = False
    return [i for i, ok in enumerate(flags) if ok]""",
    ),
    (
        "python",
        """def mean_and_variance(xs):
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return mean, var""",
    ),
    (
        "python",
        """def fit_line(xs, ys):
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    slope = (n * sxy - sx * sy) / (n * sxx - sx ** 2)
    return slope, (sy - slope * sx) / n""",
    ),
    (
        "python",
        """def transpose(m):
    return [list(row) for row in zip(*m)]

a = [[1, 2, 3], [4, 5, 6]]
print(transpose(a))""",
    ),
    (
        "python",
        """def binary_search(items, target):
    lo, hi = 0, len(items) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1""",
    ),
    (
        "python",
        """counts = {}
for word in text.split():
    counts[word] = counts.get(word, 0) + 1
top = sorted(counts, key=counts.get, reverse=True)[:5]""",
    ),
    (
        "python",
        """class Vector:
    def __init__(self, x, y):
        self.x, self.y = x, y

    def dot(self, other):
        return self.x * other.x + self.y * other.y""",
    ),
    (
        "python",
        """def celsius_to_kelvin(c):
    if c < -273.15:
        raise ValueError("below absolute zero")
    return c + 273.15""",
    ),
    (
        "c",
        """int sum_array(const int *a, int n) {
    int total = 0;
    for (int i = 0; i < n; i++) {
        total += a[i];
    }
    return total;
}""",
    ),
    (
        "c",
        """void swap(int *p, int *q) {
    int tmp = *p;
    *p = *q;
    *q = tmp;
}""",
    ),
    (
        "c",
        """struct point {
    double x;
    double y;
};

double norm2(struct point p) {
    return p.x * p.x + p.y * p.y;
}""",
    ),
    (
        "c",
        """unsigned long factorial(unsigned n) {
    unsigned long r = 1;
    while (n > 1) {
        r *= n--;
    }
    return r;
}""",
    ),
    (
        "c",
        """#include <stdio.h>

int main(void) {
    for (int k = 1; k <= 5; k++)
        printf("%d squared is %d\\n", k, k * k);
    return 0;
}""",
    ),
    (
        "c",
        """int count_bits(unsigned v) {
    int n = 0;
    for (; v; v >>= 1)
        n += v & 1u;
    return n;
}""",
    ),
    (
        "c",
        """double mean(const double *x, size_t n) {
    double s = 0.0;
    for (size_t i = 0; i < n; i++)
        s += x[i];
    return s / n;
}""",
    ),
    (
        "bash",
        """for f in data/*.csv; do
    echo "$f has $(wc -l < "$f") lines"
done""",
    ),
    (
        "bash",
        """mkdir -p results/plots
python analyse.py --input raw.csv --out results/
ls -l results""",
    ),
    (
        "bash",
        """cut -d, -f2 grades.csv | sort -n | uniq -c | sort -rn | head""",
    ),
    (
        "bash",
        """find . -name "*.log" -mtime +30 -print
grep -c "ERROR" run.log""",
    ),
    (
        "bash",
        """tar -czf backup.tar.gz notes/ figures/
tar -tzf backup.tar.gz | head -n 5""",
    ),
    (
        "bash",
        """name="lab3"
echo "Preparing $name"
cp template.tex "$name.tex"
head -n 3 "$name.tex" """,
    ),
    (
        "sql",
        """SELECT student, AVG(score) AS mean_score
FROM quiz
GROUP BY student
HAVING COUNT(*) >= 3
ORDER BY mean_score DESC;""",
    ),
    (
        "sql",
        """SELECT s.name, c.title
FROM enrolment AS e
JOIN student AS s ON s.id = e.student_id
JOIN course AS c ON c.id = e.course_id;""",
    ),
    (
        "javascript",
        """function clamp(x, lo, hi) {
  return Math.min(hi, Math.max(lo, x));
}

console.log(clamp(12, 0, 10));""",
    ),
    (
        "javascript",
        """const squares = [1, 2, 3, 4].map((n) => n * n);
const total = squares.reduce((a, b) => a + b, 0);
console.log(total);""",
    ),
    (
        "python",
        """import math

def projectile_range(v, angle_deg, g=9.81):
    a = math.radians(angle_deg)
    return v * v * math.sin(2 * a) / g""",
    ),
    (
        "python",
        """def half_life_left(n0, t, t_half):
    return n0 * 0.5 ** (t / t_half)

print(half_life_left(1000, 11460, 5730))""",
    ),
    (
        "python",
        """def moles(mass_g, molar_mass):
    return mass_g / molar_mass

print(round(moles(18.0, 18.015), 3))""",
    ),
    (
        "python",
        """def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a""",
    ),
    (
        "python",
        """def bubble_sort(items):
    items = list(items)
    for i in range(len(items)):
        for j in range(len(items) - 1 - i):
            if items[j] > items[j + 1]:
                items[j], items[j + 1] = items[j + 1], items[j]
    return items""",
    ),
    (
        "python",
        """def is_palindrome(s):
    cleaned = [ch.lower() for ch in s if ch.isalnum()]
    return cleaned == cleaned[::-1]""",
    ),
    (
        "bash",
        """export OMP_NUM_THREADS=4
./simulate --steps 1000 > run1.txt
tail -n 3 run1.txt""",
    ),
    (
        "c",
        """int is_prime(int n) {
    if (n < 2) return 0;
    for (int d = 2; d * d <= n; d++)
        if (n % d == 0) return 0;
    return 1;
}""",
    ),
]

# ----------------------------------------------------------------- glossary
# (term, definition): original definitions.

GLOSSARY: list[tuple[str, str]] = [
    (
        "Derivative",
        "The limit of the ratio of the change in a function to the change in its input; it gives the slope of the tangent line.",
    ),
    (
        "Integral",
        "A limit of sums that measures accumulated change, such as the area under a curve.",
    ),
    (
        "Limit",
        "The value that a function approaches as its input gets arbitrarily close to a chosen point.",
    ),
    ("Matrix", "A rectangular array of numbers arranged in rows and columns."),
    (
        "Determinant",
        "A single number computed from a square matrix that tells whether the matrix can be inverted.",
    ),
    (
        "Eigenvalue",
        "A scalar by which a matrix stretches a special direction called an eigenvector.",
    ),
    (
        "Vector",
        "A quantity with both a size and a direction, often written as a column of numbers.",
    ),
    ("Scalar", "A quantity described by a single number and, in physics, a unit."),
    ("Domain", "The set of all inputs for which a function is defined."),
    ("Range", "The set of all outputs that a function actually produces."),
    ("Asymptote", "A line that a curve approaches without ever reaching it."),
    ("Polynomial", "A sum of terms, each a constant times a whole-number power of a variable."),
    ("Logarithm", "The exponent to which a base must be raised to give a stated number."),
    ("Sequence", "An ordered list of numbers, usually given by a rule."),
    ("Series", "The sum of the terms of a sequence."),
    ("Probability", "A number between zero and one that measures how likely an event is."),
    ("Mean", "The sum of a set of values divided by how many values there are."),
    ("Variance", "The average squared distance of the values in a data set from their mean."),
    ("Hypothesis", "A statement about the world that an experiment is designed to test."),
    (
        "Isotope",
        "One of several forms of an element that have the same number of protons but different numbers of neutrons.",
    ),
    ("Ion", "An atom or molecule that carries a net electric charge."),
    (
        "Mole",
        "The amount of substance that contains as many particles as there are atoms in twelve grams of carbon-12.",
    ),
    ("Molarity", "The number of moles of solute in one litre of solution."),
    ("Catalyst", "A substance that speeds up a reaction without being used up."),
    (
        "Equilibrium",
        "A state in which the forward and reverse rates of a process are equal and the amounts stop changing.",
    ),
    ("Half-life", "The time needed for half of the nuclei in a radioactive sample to decay."),
    ("Velocity", "The rate of change of position, with a direction."),
    ("Acceleration", "The rate of change of velocity."),
    ("Momentum", "The product of an object's mass and its velocity."),
    ("Frequency", "The number of complete cycles of a wave that pass a point each second."),
    ("Wavelength", "The distance between two matching points on neighbouring cycles of a wave."),
    ("Amplitude", "The largest displacement of a wave from its rest position."),
    ("Algorithm", "A finite list of precise steps that solves a class of problems."),
    ("Variable", "A name that stands for a value which may change."),
    ("Loop", "A piece of code that repeats until a condition is met."),
    (
        "Recursion",
        "A method in which a function is defined in terms of a smaller instance of itself.",
    ),
    ("Compiler", "A program that translates source code into machine instructions."),
    ("Bit", "The smallest unit of information, holding either zero or one."),
    (
        "Entropy",
        "A measure of how many microscopic arrangements are consistent with a macroscopic state.",
    ),
    ("Torque", "The turning effect of a force about an axis, equal to force times lever arm."),
    ("Resistance", "The opposition of a material to the flow of electric current."),
    ("Refraction", "The bending of a wave as it passes from one medium into another."),
    ("Enzyme", "A protein that acts as a catalyst for a reaction in a living cell."),
    (
        "Tangent",
        "A line that touches a curve at one point and has the same direction as the curve there.",
    ),
    (
        "Corollary",
        "A result that follows almost immediately from a theorem that has just been proved.",
    ),
    ("Residual", "The difference between an observed value and the value predicted by a model."),
    ("Outlier", "A data point that lies unusually far from the rest of the values."),
    ("Dimensionless", "Describing a quantity that has no unit, such as a ratio of two lengths."),
    (
        "Significant figures",
        "The digits of a measured number that carry information about its precision.",
    ),
]

# ----------------------------------------------------------------- latex-structure content

LONG_TITLES = [
    "Estimating Rates of Change from Tables of Measured Values Collected During Class Experiments",
    "Reading Graphs of Velocity Against Time and Recovering the Distance Travelled by an Object",
    "How Rounding Errors Accumulate When a Long Calculation Is Carried Out on a Pocket Calculator",
    "Choosing Between a Linear Model and an Exponential Model for a Small Set of Experimental Data",
    "A Careful Look at Units, Dimensions and the Common Mistakes Made When Converting Between Them",
    "From Words to Equations: Translating Short Problem Statements into Algebra Without Losing Information",
    "Why Some Chemical Reactions Reach Equilibrium and What the Equilibrium Constant Tells Us About Them",
    "Sketching Polynomial Functions by Finding Intercepts, Turning Points and the Long-Run Behaviour",
    "Interpreting the Slope and the Intercept of a Line of Best Fit in the Context of an Experiment",
    "Sampling, Uncertainty and the Difference Between a Mistake in the Method and a Random Fluctuation",
    "Counting Techniques for Arrangements and Selections, with Worked Examples from Games of Chance",
    "Solving Systems of Linear Equations by Elimination and Recognising When No Solution Exists at All",
    "Writing a Clear Laboratory Report That Another Student Could Follow Without Asking Any Questions",
    "Checking an Answer for Reasonableness Before Submitting It: Estimates, Limiting Cases and Symmetry",
]

SECTION_INTROS = [
    "A table of values is often the first form in which a rate of change is met. The difference quotient between neighbouring rows is the best guide to the behaviour of the underlying quantity.",
    "The area between a velocity graph and the time axis equals the displacement. Signs matter: regions below the axis count as motion in the opposite direction.",
    "Every rounded intermediate result introduces a small error, and these errors can grow. Keeping extra digits until the final step is a simple habit that avoids most of the damage.",
    "A straight line and a curve that bends upward can both pass close to a handful of points. The choice should rest on the process that produced the data, not only on how well the curve fits.",
    "A numerical answer without a unit is incomplete, and a unit with the wrong dimensions is a sign of an error earlier in the work. Checking both takes only a few seconds.",
    "Most algebra mistakes are made before any algebra is done, when a sentence is turned into an equation. Writing down what every symbol stands for removes much of the risk.",
    "A reversible reaction does not stop; the forward and reverse rates become equal. The equilibrium constant compares the amounts of products and reactants once that balance has been reached.",
    "The shape of a polynomial can be predicted from a few facts: its degree, its roots, and the sign of the leading coefficient. A rough sketch based on these facts is often enough.",
    "The slope of a fitted line is a rate with units, and the intercept is a starting value. Reading both in the language of the experiment turns a formula into a statement about the world.",
    "No measurement is exact, and repeated readings scatter around a typical value. A systematic offset, by contrast, moves every reading in the same direction.",
    "Counting problems become easier once it is clear whether order matters and whether repetition is allowed. The two questions fix which formula applies.",
    "Elimination replaces a pair of equations by an equivalent pair in which one unknown has disappeared. A row that reduces to zero equals a nonzero number reveals that the system has no solution.",
    "A good report states the question, the method, the data and the conclusion, in that order. Each part should be short enough to read in a minute.",
    "Before an answer is handed in, a quick estimate, a limiting case or a symmetry argument can reveal a slip. Such checks are cheap and surprisingly effective.",
]

# (run-in heading, body sentence(s)). Headings end in . ? : or ! or are long enough to count as sentences.
RUN_IN: list[tuple[str, str]] = [
    (
        "Check the units before you start.",
        "Convert every quantity to a single system of units before substituting any numbers.",
    ),
    (
        "Why does the sign change?",
        "Dividing both sides of an inequality by a negative number reverses its direction.",
    ),
    (
        "Remember the order of operations:",
        "Brackets first, then powers, then multiplication and division, and last addition and subtraction.",
    ),
    (
        "Do not round early!",
        "Carry at least two more digits than you need until the last line of the calculation.",
    ),
    (
        "Write down what you know.",
        "A short list of given quantities and unknowns often shows which formula is needed.",
    ),
    (
        "Is the answer reasonable?",
        "Compare the size of the result with a rough estimate made in your head.",
    ),
    (
        "Draw a picture first.",
        "Even a crude sketch of the situation prevents many sign and direction errors.",
    ),
    (
        "State the assumptions clearly.",
        "Ignoring air resistance or treating a gas as ideal is acceptable only if it is said aloud.",
    ),
    (
        "Start from the definition.",
        "When a formula is forgotten, the definition of the quantity usually rebuilds it.",
    ),
    (
        "Label every axis and every curve.",
        "A graph without labels and units cannot be read by anyone else.",
    ),
    (
        "One change at a time.",
        "Vary a single quantity in each trial so that the effect can be traced to its cause.",
    ),
    (
        "Keep a record of failed attempts?",
        "They explain later why a particular approach was abandoned.",
    ),
    (
        "Use consistent notation throughout the whole solution.",
        "Switching between two symbols for the same quantity is a frequent source of confusion.",
    ),
    (
        "Look for a pattern in the first few cases.",
        "A conjecture formed from small examples can then be tested on a larger one.",
    ),
    (
        "Read the question twice.",
        "The second reading usually reveals a condition that the first one skipped.",
    ),
    (
        "Substitute back to check.",
        "Putting the solution into the original equation catches arithmetic slips and extraneous roots.",
    ),
    (
        "Estimate the uncertainty.",
        "Half of the smallest scale division is a sensible starting point for a single reading.",
    ),
    (
        "Take a break when stuck:",
        "A short walk often clears the way to an idea that hours at the desk did not.",
    ),
    (
        "Explain the result in words.",
        "A final sentence in plain language shows that the numbers have been understood.",
    ),
    (
        "Mind the difference between mass and weight.",
        "Mass is measured in kilograms and weight, a force, in newtons.",
    ),
    (
        "Collect like terms before factoring.",
        "Neater expressions have fewer chances for sign errors.",
    ),
    (
        "Compare with a limiting case.",
        "Setting a parameter to zero or letting it grow often gives a result that is known already.",
    ),
    (
        "Report the final answer with its unit.",
        "A number alone is not an answer in any science course.",
    ),
    (
        "Share the work with a partner?",
        "Explaining a step aloud is the quickest way to find a gap in it.",
    ),
]
PLAIN_RUN_IN = [
    ("Notation", "Vectors are written in bold type and scalars in italics."),
    (
        "Caution",
        "Calculators may be set to degrees or to radians; check before using trigonometric functions.",
    ),
    ("Reminder", "Bring the data sheet and a ruler to the next lab session."),
    ("Convention", "Angles are measured anticlockwise from the positive horizontal axis."),
]

EXAMPLES = [
    (
        "A cart rolls 12 m down a ramp in 4.0 s, starting from rest. Find its acceleration.",
        "Use d = (1/2) a t^{2|o}, so a = 2d / t^{2|o} = 2(12) / (4.0)^{2|o} = 1.5 m/s^{2|u}.",
    ),
    (
        "A solution is made by dissolving 18 g of salt in water to make 250 cm^{3|u}. Find the concentration in g per cm^{3|u}.",
        "The concentration is 18 / 250 = 0.072 g/cm^{3|u}.",
    ),
    (
        "The sum of two numbers is 25 and their difference is 7. Find both numbers.",
        "Adding the two equations gives twice the larger number, 32, so the larger number is 16 and the smaller is 9.",
    ),
    (
        "A data set has the values 4.2, 5.1 and 6.0. Find the mean.",
        "The mean is (4.2 + 5.1 + 6.0) / 3 = 5.1.",
    ),
    (
        "A sample of 80 g of a radioactive isotope has a half-life of 6 hours. How much remains after 18 hours?",
        "Eighteen hours is 3 half-lives, so the mass is 80 / 2^{3|o} = 10 g.",
    ),
    (
        "A rectangle has a perimeter of 30 cm and one side of 4 cm. Find its area.",
        "The other side is 30/2 - 4 = 11 cm, so the area is 44 cm^{2|u}.",
    ),
]

TAKEAWAYS = [
    [
        "A rate of change is a ratio of two differences.",
        "Neighbouring table rows give the best estimate of a local rate.",
        "State the unit of every rate you report.",
    ],
    [
        "Area under a velocity graph is displacement.",
        "Regions below the axis count as negative.",
        "Check the answer against a rough area estimate.",
    ],
    [
        "Round only at the end of a calculation.",
        "Keep two spare digits in every intermediate step.",
        "Report the answer with the precision of the data.",
    ],
    [
        "Choose the model from the process, not from the plot alone.",
        "Compare residuals, not only the correlation.",
        "A good fit inside the data says little outside it.",
    ],
    [
        "Every equation must balance in its units.",
        "Convert before you substitute.",
        "Write the unit on every line of the work.",
    ],
]

QUESTIONS = [
    [
        "Explain in one sentence why the slope of a position graph is a velocity.",
        "A graph is flat between t = 2 s and t = 5 s. What does that say about the motion?",
    ],
    [
        "Why is it risky to round 1/3 to 0.33 in the first line of a long calculation?",
        "How many significant figures does 0.00450 have?",
    ],
    [
        "Name one situation in which a straight line is the wrong model for data.",
        "What does a negative residual tell you about a data point?",
    ],
    [
        "A student writes 5 m + 3 s. What is wrong with this expression?",
        "Convert 72 km/h to metres per second.",
    ],
    [
        "State two assumptions behind the ideal gas law.",
        "What quantity stays constant at equilibrium: the amounts or the rates?",
    ],
]

# ----------------------------------------------------------------- tables (cells use script markup)

DATA_TABLES = [
    (
        "Measurements of a falling steel ball",
        [
            ["Trial", "Height (m)", "Time (s)", "Speed (m/s)"],
            ["1", "0.50", "0.32", "3.1"],
            ["2", "1.00", "0.45", "4.4"],
            ["3", "1.50", "0.55", "5.4"],
            ["4", "2.00", "0.64", "6.3"],
            ["5", "2.50", "0.71", "7.0"],
        ],
        1,
        0,
    ),
    (
        "Selected properties of four metals",
        [
            ["Metal", "Symbol", "Density (g/cm^{3|u})", "Melting point (K)"],
            ["Aluminium", "Al", "2.70", "933"],
            ["Copper", "Cu", "8.96", "1358"],
            ["Iron", "Fe", "7.87", "1811"],
            ["Zinc", "Zn", "7.14", "693"],
        ],
        1,
        1,
    ),
    (
        "Summary of quiz scores by group",
        [
            ["Group", "Students", "Mean score", "Standard deviation"],
            ["Morning", "28", "71.4", "9.2"],
            ["Afternoon", "31", "68.9", "10.5"],
            ["Evening", "19", "74.2", "8.1"],
        ],
        1,
        1,
    ),
    (
        "Half-lives of some radioactive isotopes",
        [
            ["Isotope", "Protons", "Neutrons", "Half-life"],
            ["^{14|n}C", "6", "8", "5730 years"],
            ["^{60|n}Co", "27", "33", "5.27 years"],
            ["^{131|n}I", "53", "78", "8.0 days"],
            ["^{238|n}U", "92", "146", "4.5 billion years"],
        ],
        1,
        1,
    ),
]

LAYOUT_TABLES = [
    (
        "side-by-side",
        [
            [
                "Reading list. Chapter 3 of the course notes, the practice sheet on rates of change, and the short article on measurement error.",
                "Schedule. Lecture on Monday, laboratory on Wednesday, and a quiz at the start of class on Friday morning.",
            ]
        ],
    ),
    (
        "form",
        [
            ["Name:", ""],
            ["Student number:", ""],
            ["Date of laboratory:", ""],
        ],
    ),
    (
        "signatures",
        [
            ["Student signature", "Instructor signature"],
            ["", ""],
        ],
    ),
    (
        "given-find",
        [
            [
                "Given. The mass is 2.0 kg, the height is 5.0 m and the starting speed is zero.",
                "Find. The speed of the mass just before it reaches the ground.",
            ]
        ],
    ),
]

# ----------------------------------------------------------------- hyphenation prose

COMPOUNDS = [
    "well-known", "x-axis", "y-axis", "least-squares", "long-term", "real-valued",
    "two-dimensional", "first-order", "second-order", "high-energy", "low-temperature",
    "rate-limiting", "self-consistent", "cross-sectional", "so-called", "time-dependent",
    "half-life", "free-fall", "right-angled", "closed-form", "well-defined",
    "state-of-the-art", "order-of-magnitude", "non-negative", "steady-state", "point-by-point",
    "inverse-square", "frictionless", "single-valued", "full-scale",
]  # fmt: skip

SUBJECTS = [
    "The experimental physicist", "A carefully calibrated spectrometer",
    "Every undergraduate chemistry student", "The {c} method", "An electromagnetic wave",
    "The thermodynamic equilibrium of the system", "A mathematical proof by contradiction",
    "The measurement of gravitational acceleration", "Our characteristic approximation",
    "The theoretical explanation", "A representative sample of the population",
    "The computational simulation", "An unexpectedly large uncertainty",
    "The mathematical description of {c} behaviour", "Each independent observation",
    "The temperature distribution inside the container", "A nonlinear differential equation",
    "The relationship between pressure and volume", "Independent verification of the result",
    "The geometrical interpretation of the {c} solution",
]  # fmt: skip

VERBS = [
    "demonstrates", "underestimates", "approximates", "characterizes", "investigates",
    "complicates", "accompanies", "determines", "illustrates", "represents", "contradicts",
    "transforms", "intensifies", "recalculates", "distinguishes", "reconstructs",
]  # fmt: skip

OBJECTS = [
    "the relationship between temperature and pressure",
    "the long-term behaviour of the oscillation",
    "the unexpected distribution of the experimental errors",
    "an extraordinarily small displacement of the apparatus",
    "the fundamental difference between velocity and acceleration",
    "the characteristic wavelength of the transmitted radiation",
    "a considerably more complicated mathematical structure",
    "the approximate magnitude of the electromagnetic interaction",
    "the transformation of kinetic energy into thermal energy",
    "the statistical significance of the observed differences",
    "the interpretation of the {c} coefficients",
    "the gradual disappearance of the interference pattern",
    "an accurate representation of the underlying phenomenon",
    "the numerical stability of the {c} algorithm",
    "the instantaneous rate of change at the origin",
]  # fmt: skip

ADVERBIALS = [
    "under ideal laboratory conditions", "whenever the measurements are reproducible",
    "throughout the entire experiment", "in a surprisingly straightforward manner",
    "regardless of the initial configuration", "after considerable theoretical development",
    "with reasonable computational resources", "as the temperature gradually increases",
    "until the instrument reaches thermal equilibrium", "despite the unavoidable experimental noise",
    "along the horizontal {c} direction", "for every sufficiently large value of the parameter",
    "provided the assumptions remain reasonable", "in the neighbourhood of the critical point",
]  # fmt: skip

SECOND = [
    "Consequently, the investigators recommended an independent confirmation of the {c} estimate.",
    "Furthermore, the uncertainties were propagated through every intermediate calculation.",
    "Nevertheless, an accurate interpretation requires considerable mathematical sophistication.",
    "Unfortunately, the transmission of the signal introduced an unanticipated systematic distortion.",
    "Experimentally, the temperature was maintained within a fraction of a degree throughout.",
    "Mathematically, the argument is straightforward but the notation is unavoidably cumbersome.",
    "In particular, the {c} curve passes remarkably close to every measured point.",
    "Occasionally, an apparently insignificant detail changes the entire qualitative picture.",
    "Therefore, the proposed explanation should be regarded as provisional rather than definitive.",
    "Interestingly, the same characteristic pattern reappears in many unrelated physical systems.",
]  # fmt: skip


def hyphenation_paragraphs(rng: random.Random, count: int, sentences: int = 9) -> list[str]:
    """Paragraphs of long-word, compound-rich prose: a grammar over the pools above."""
    paras = []
    for _ in range(count):
        out = []
        for k in range(sentences):
            c = rng.choice(COMPOUNDS)
            if k % 3 == 2:
                out.append(rng.choice(SECOND).format(c=c))
            else:
                subj = rng.choice(SUBJECTS).format(c=c)
                obj = rng.choice(OBJECTS).format(c=rng.choice(COMPOUNDS))
                adv = rng.choice(ADVERBIALS).format(c=rng.choice(COMPOUNDS))
                out.append(f"{subj} {rng.choice(VERBS)} {obj} {adv}.")
        paras.append(" ".join(out))
    return paras
